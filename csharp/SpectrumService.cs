// ---------------------------------------------------------------------------
//  SpectrumService  -  Independent C# spectrum-analysis module
//
//  A standalone TCP server that receives a 2D seismic slice (or an ROI cut out
//  of one) from the Python host application, computes the *average amplitude
//  spectrum* over all traces, and returns it.
// ---------------------------------------------------------------------------

using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace Waverity.Spectrum
{
    // -----------------------------------------------------------------------
    //  Digital signal processing helpers
    // -----------------------------------------------------------------------
    internal static class Dsp
    {
        public const int WindowNone    = 0;
        public const int WindowHann    = 1;
        public const int WindowHamming = 2;

        /// <summary>Smallest power of two that is >= n (minimum 2).</summary>
        public static int NextPow2(int n)
        {
            int p = 2;
            while (p < n) p <<= 1;
            return p;
        }

        /// <summary>Taper coefficients for a window of the given length.</summary>
        public static double[] MakeWindow(int kind, int n)
        {
            double[] w = new double[n];
            if (kind == WindowNone || n < 2)
            {
                for (int i = 0; i < n; i++) w[i] = 1.0;
                return w;
            }

            double denom = n - 1;
            for (int i = 0; i < n; i++)
            {
                double phase = 2.0 * Math.PI * i / denom;
                if (kind == WindowHamming)
                    w[i] = 0.54 - 0.46 * Math.Cos(phase);
                else                       // Hann is the default taper
                    w[i] = 0.5 * (1.0 - Math.Cos(phase));
            }
            return w;
        }

        /// <summary>
        /// In-place iterative radix-2 Cooley-Tukey FFT.  Length must be a
        /// power of two; the caller is responsible for zero padding.
        /// </summary>
        public static void Fft(double[] re, double[] im)
        {
            int n = re.Length;
            if (n != im.Length) throw new ArgumentException("re/im length mismatch");
            if ((n & (n - 1)) != 0) throw new ArgumentException("length must be a power of two");

            // Bit-reversal permutation.
            for (int i = 1, j = 0; i < n; i++)
            {
                int bit = n >> 1;
                for (; (j & bit) != 0; bit >>= 1) j ^= bit;
                j |= bit;

                if (i < j)
                {
                    double t;
                    t = re[i]; re[i] = re[j]; re[j] = t;
                    t = im[i]; im[i] = im[j]; im[j] = t;
                }
            }

            // Butterflies, stage by stage.
            for (int len = 2; len <= n; len <<= 1)
            {
                int half = len >> 1;
                double ang = -2.0 * Math.PI / len;
                double wr = Math.Cos(ang);
                double wi = Math.Sin(ang);

                for (int start = 0; start < n; start += len)
                {
                    double cr = 1.0, ci = 0.0;
                    for (int k = 0; k < half; k++)
                    {
                        int a = start + k;
                        int b = a + half;

                        double vr = re[b] * cr - im[b] * ci;
                        double vi = re[b] * ci + im[b] * cr;

                        re[b] = re[a] - vr;
                        im[b] = im[a] - vi;
                        re[a] = re[a] + vr;
                        im[a] = im[a] + vi;

                        double next = cr * wr - ci * wi;
                        ci = cr * wi + ci * wr;
                        cr = next;
                    }
                }
            }
        }

        /// <summary>
        /// Average single-sided amplitude spectrum of a trace gather.
        /// </summary>
        public static float[] AverageAmplitudeSpectrum(
            float[] data, int nTraces, int nSamples, int windowKind, out int nfft)
        {
            nfft = NextPow2(nSamples);
            int nFreq = nfft / 2 + 1;

            double[] taper = MakeWindow(windowKind, nSamples);
            double[] accum = new double[nFreq];
            double[] re = new double[nfft];
            double[] im = new double[nfft];

            double taperGain = 0.0;
            for (int i = 0; i < nSamples; i++) taperGain += taper[i];
            if (taperGain <= 0.0) taperGain = nSamples;

            int usedTraces = 0;
            for (int t = 0; t < nTraces; t++)
            {
                int offset = t * nSamples;

                double mean = 0.0;
                for (int i = 0; i < nSamples; i++) mean += data[offset + i];
                mean /= nSamples;

                bool finite = true;
                for (int i = 0; i < nSamples; i++)
                {
                    double v = (data[offset + i] - mean) * taper[i];
                    if (double.IsNaN(v) || double.IsInfinity(v)) { finite = false; break; }
                    re[i] = v;
                    im[i] = 0.0;
                }
                if (!finite) continue;

                for (int i = nSamples; i < nfft; i++) { re[i] = 0.0; im[i] = 0.0; }

                Fft(re, im);

                for (int k = 0; k < nFreq; k++)
                {
                    double mag = Math.Sqrt(re[k] * re[k] + im[k] * im[k]);
                    if (k > 0 && k < nfft / 2) mag *= 2.0;
                    accum[k] += mag / taperGain;
                }
                usedTraces++;
            }

            if (usedTraces == 0) usedTraces = 1;

            float[] result = new float[nFreq];
            for (int k = 0; k < nFreq; k++) result[k] = (float)(accum[k] / usedTraces);
            return result;
        }
    }

    // -----------------------------------------------------------------------
    //  TCP front end
    // -----------------------------------------------------------------------
    internal static class Program
    {
        private const int ProtocolVersion = 1;
        private const int MaxSamples      = 1 << 20;
        private const long MaxPayload     = 512L << 20;

        private static int Main(string[] args)
        {
            int port = 0;

            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--selftest") return SelfTest();
                if (args[i] == "--port" && i + 1 < args.Length)
                {
                    if (!int.TryParse(args[i + 1], out port))
                    {
                        Console.Error.WriteLine("ERROR invalid --port value");
                        return 2;
                    }
                    i++;
                }
            }

            TcpListener listener;
            try
            {
                listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("ERROR cannot listen: " + ex.Message);
                return 3;
            }

            int actualPort = ((IPEndPoint)listener.LocalEndpoint).Port;
            Console.WriteLine("PORT " + actualPort);
            Console.WriteLine("READY");
            Console.Out.Flush();

            while (true)
            {
                TcpClient client;
                try { client = listener.AcceptTcpClient(); }
                catch (SocketException) { break; }

                Thread worker = new Thread(HandleClient);
                worker.IsBackground = true;
                worker.Start(client);
            }
            return 0;
        }

        private static void HandleClient(object state)
        {
            TcpClient client = (TcpClient)state;
            try
            {
                client.NoDelay = true;
                using (NetworkStream stream = client.GetStream())
                {
                    while (ServeOneRequest(stream)) { }
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("WARN client dropped: " + ex.Message);
            }
            finally
            {
                try { client.Close(); } catch { }
            }
        }

        private static bool ServeOneRequest(NetworkStream stream)
        {
            byte[] magic = new byte[4];
            if (!TryReadExactly(stream, magic, 4)) return false;

            if (Encoding.ASCII.GetString(magic) != "SPEC")
            {
                WriteError(stream, 1, "bad magic, expected SPEC");
                return false;
            }

            int version, nTraces, nSamples, windowKind;
            double dt;
            try
            {
                version    = ReadInt32(stream);
                nTraces    = ReadInt32(stream);
                nSamples   = ReadInt32(stream);
                dt         = ReadFloat64(stream);
                windowKind = ReadInt32(stream);
            }
            catch (EndOfStreamException)
            {
                return false;
            }

            if (version != ProtocolVersion)
            {
                WriteError(stream, 2, "unsupported protocol version " + version);
                return false;
            }
            if (nTraces <= 0 || nSamples <= 1 || nSamples > MaxSamples)
            {
                WriteError(stream, 3, "invalid geometry " + nTraces + "x" + nSamples);
                return false;
            }

            long count = (long)nTraces * nSamples;
            if (count * 4L > MaxPayload)
            {
                WriteError(stream, 4, "payload too large");
                return false;
            }

            byte[] raw = new byte[count * 4L];
            if (!TryReadExactly(stream, raw, raw.Length))
            {
                return false;
            }

            float[] data = new float[count];
            Buffer.BlockCopy(raw, 0, data, 0, raw.Length);
            raw = null;

            if (dt <= 0.0) dt = 0.004;

            try
            {
                int nfft;
                float[] amp = Dsp.AverageAmplitudeSpectrum(
                    data, nTraces, nSamples, windowKind, out nfft);
                double df = 1.0 / (nfft * dt);
                WriteOk(stream, amp, df);
            }
            catch (Exception ex)
            {
                WriteError(stream, 5, ex.Message);
                return false;
            }
            return true;
        }

        // -- response writers ------------------------------------------------

        private static void WriteOk(NetworkStream stream, float[] amp, double df)
        {
            byte[] payload = new byte[amp.Length * 4];
            Buffer.BlockCopy(amp, 0, payload, 0, payload.Length);

            MemoryStream buf = new MemoryStream();
            buf.Write(Encoding.ASCII.GetBytes("SPCR"), 0, 4);
            WriteInt32(buf, 0);
            WriteInt32(buf, amp.Length);
            WriteFloat64(buf, df);
            buf.Write(payload, 0, payload.Length);

            byte[] all = buf.ToArray();
            stream.Write(all, 0, all.Length);
            stream.Flush();
        }

        private static void WriteError(NetworkStream stream, int status, string message)
        {
            try
            {
                byte[] msg = Encoding.UTF8.GetBytes(message);
                MemoryStream buf = new MemoryStream();
                buf.Write(Encoding.ASCII.GetBytes("SPCR"), 0, 4);
                WriteInt32(buf, status);
                WriteInt32(buf, msg.Length);
                buf.Write(msg, 0, msg.Length);

                byte[] all = buf.ToArray();
                stream.Write(all, 0, all.Length);
                stream.Flush();
            }
            catch { }

            Console.Error.WriteLine("ERROR " + status + ": " + message);
        }

        // -- little-endian primitives ----------------------------------------

        private static bool TryReadExactly(Stream s, byte[] buffer, int count)
        {
            int done = 0;
            while (done < count)
            {
                int n = s.Read(buffer, done, count - done);
                if (n <= 0) return false;
                done += n;
            }
            return true;
        }

        private static void ReadExactly(Stream s, byte[] buffer, int count)
        {
            if (!TryReadExactly(s, buffer, count)) throw new EndOfStreamException();
        }

        private static int ReadInt32(Stream s)
        {
            byte[] b = new byte[4];
            ReadExactly(s, b, 4);
            if (!BitConverter.IsLittleEndian) Array.Reverse(b);
            return BitConverter.ToInt32(b, 0);
        }

        private static double ReadFloat64(Stream s)
        {
            byte[] b = new byte[8];
            ReadExactly(s, b, 8);
            if (!BitConverter.IsLittleEndian) Array.Reverse(b);
            return BitConverter.ToDouble(b, 0);
        }

        private static void WriteInt32(Stream s, int value)
        {
            byte[] b = BitConverter.GetBytes(value);
            if (!BitConverter.IsLittleEndian) Array.Reverse(b);
            s.Write(b, 0, 4);
        }

        private static void WriteFloat64(Stream s, double value)
        {
            byte[] b = BitConverter.GetBytes(value);
            if (!BitConverter.IsLittleEndian) Array.Reverse(b);
            s.Write(b, 0, 8);
        }

        // -- self test --------------------------------------------------------

        private static int SelfTest()
        {
            const int nSamples = 512;
            const int nTraces  = 8;
            const double dt    = 0.004;
            const double f0    = 25.0;

            float[] data = new float[nTraces * nSamples];
            for (int t = 0; t < nTraces; t++)
                for (int i = 0; i < nSamples; i++)
                    data[t * nSamples + i] = (float)Math.Sin(2.0 * Math.PI * f0 * i * dt);

            int nfft;
            float[] amp = Dsp.AverageAmplitudeSpectrum(
                data, nTraces, nSamples, Dsp.WindowHann, out nfft);
            double df = 1.0 / (nfft * dt);

            int peak = 0;
            for (int k = 1; k < amp.Length; k++) if (amp[k] > amp[peak]) peak = k;
            double peakHz = peak * df;

            Console.WriteLine("nfft      = " + nfft);
            Console.WriteLine("df        = " + df.ToString("F4") + " Hz");
            Console.WriteLine("peak bin  = " + peak + "  ->  " + peakHz.ToString("F2") + " Hz");
            Console.WriteLine("peak amp  = " + amp[peak].ToString("F4"));

            bool freqOk = Math.Abs(peakHz - f0) <= df;
            bool ampOk  = Math.Abs(amp[peak] - 1.0) < 0.15;

            Console.WriteLine(freqOk ? "PASS frequency" : "FAIL frequency");
            Console.WriteLine(ampOk  ? "PASS amplitude" : "FAIL amplitude");
            return (freqOk && ampOk) ? 0 : 1;
        }
    }
}
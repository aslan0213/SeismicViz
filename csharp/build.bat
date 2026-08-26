@echo off
REM Builds the independent C# spectrum module.
REM No .NET SDK or NuGet package is required: the source targets C# 5, so the
REM compiler that ships with the .NET Framework can build it.

setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (
    echo ERROR: no C# compiler found at %CSC%
    echo Install the .NET SDK and build SpectrumService.cs with 'dotnet build'.
    exit /b 1
)

cd /d "%~dp0"
if not exist bin mkdir bin

"%CSC%" -nologo -optimize+ -warn:4 -target:exe -out:bin\SpectrumService.exe SpectrumService.cs
if errorlevel 1 exit /b 1

echo Built csharp\bin\SpectrumService.exe
echo Running self test...
bin\SpectrumService.exe --selftest
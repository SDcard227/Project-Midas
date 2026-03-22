@echo off
cd /d "%~dp0"

:menu
cls
echo.
echo   ================================================
echo    Project Midas
echo   ================================================
echo.
echo   [1]  Setup           configure Midas (first time setup)
echo   [2]  Monitor         live market screen
echo   [3]  Search          lookup, screener, trade history
echo   [4]  Backtest        test against historical data
echo   [5]  Trade           start paper / live trading
echo   [6]  Fees            view and collect performance fees
echo   [7]  Exit
echo.
set /p choice="  Choose: "

if "%choice%"=="1" goto setup
if "%choice%"=="2" goto monitor
if "%choice%"=="3" goto search
if "%choice%"=="4" goto backtest
if "%choice%"=="5" goto trade
if "%choice%"=="6" goto fees
if "%choice%"=="7" exit

goto menu

:setup
py setup.py
echo.
pause
goto menu

:monitor
start "Midas Monitor" cmd /k "cd /d "%~dp0" && py monitor.py"
goto menu

:search
cls
echo.
echo   ================================================
echo    Search ^& Screener
echo   ================================================
echo.
echo   AAPL                  — signal for one ticker
echo   AAPL MSFT NVDA        — signal for multiple tickers
echo   --screen              — scan 30 popular tickers
echo   --screen --top 10     — top 10 from screener
echo   --history             — your full trade history
echo   --history AAPL        — trades for one ticker
echo   --news AAPL           — live YouTube news coverage
echo.
echo   [b]  Back
echo.
set /p cmd="  py search.py "
if /i "%cmd%"=="b" goto menu
py search.py %cmd%
echo.
pause
goto menu

:backtest
cls
echo.
echo   ================================================
echo    Backtest
echo   ================================================
echo.
echo   surge aggressive 2023
echo   climb normal 4 2022
echo   surge aggressive 8 2023
echo.
echo   [b]  Back
echo.
set /p cmd="  py backtest_run.py "
if /i "%cmd%"=="b" goto menu
py backtest_run.py %cmd%
echo.
pause
goto menu

:trade
py main.py
pause
goto menu

:fees
echo.
echo   [1]  View fees owed
echo   [2]  Collect fees (mark as paid)
echo   [3]  Back
echo.
set /p fchoice="  Choose: "
if "%fchoice%"=="1" (py fees.py && echo. && pause)
if "%fchoice%"=="2" (py fees.py collect && echo. && pause)
goto menu

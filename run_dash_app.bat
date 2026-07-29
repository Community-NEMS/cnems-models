
REM Run in browser
start "" "http://127.0.0.1:8080"

REM Open Anaconda prompt
REM call "C:\Program Files\Anaconda3\condabin\activate.bat"

REM navigate to project directory , activate new conda env, and run the app
cd %cd% && conda activate bsky && python app.py

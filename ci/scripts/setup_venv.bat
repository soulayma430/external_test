@echo off
:: ============================================================
::  setup_venv.bat  —  Création du venv Python pour Jenkins CI
::  Usage : setup_venv.bat <workspace_path>
::  Appelé par le Jenkinsfile au stage "Setup Python"
:: ============================================================

setlocal disabledelayedexpansion

SET WORKSPACE=%~1
IF "%WORKSPACE%"=="" SET WORKSPACE=%CD%

SET VENV=%WORKSPACE%\.venv
SET PIP=%VENV%\Scripts\pip.exe
SET PYTHON=%VENV%\Scripts\python.exe
SET REQ=%WORKSPACE%\ci\requirements-ci.txt

echo.
echo ====================================================
echo   WipeWash CI — Setup venv Python
echo   Workspace : %WORKSPACE%
echo ====================================================
echo.

:: ── Vérifier Python 3.11+ ───────────────────────────────────────────────────
where python >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERREUR] Python introuvable dans le PATH.
    echo Installez Python 3.11+ depuis https://python.org et cochez "Add to PATH".
    exit /b 2
)

python --version
FOR /F "tokens=2 delims= " %%V IN ('python --version 2^>^&1') DO SET PY_VER=%%V
echo Python detecte : %PY_VER%

:: ── Créer ou réutiliser le venv ──────────────────────────────────────────────
IF NOT EXIST "%VENV%\Scripts\activate.bat" (
    echo [SETUP] Creation du venv dans %VENV%...
    python -m venv "%VENV%"
    IF ERRORLEVEL 1 (
        echo [ERREUR] Impossible de creer le venv.
        exit /b 2
    )
    echo [SETUP] Venv cree.
) ELSE (
    echo [SETUP] Venv existant detecte — reutilisation.
)

:: ── Mettre à jour pip ────────────────────────────────────────────────────────
echo [SETUP] Mise a jour pip...
"%PYTHON%" -m pip install --quiet --upgrade pip
IF ERRORLEVEL 1 (
    echo [WARN] Mise a jour pip echouee (non bloquant).
)

:: ── Installer les dépendances ────────────────────────────────────────────────
IF EXIST "%REQ%" (
    echo [SETUP] Installation des dependances depuis %REQ%...
    "%PIP%" install --quiet -r "%REQ%"
    IF ERRORLEVEL 1 (
        echo [ERREUR] Installation des dependances echouee.
        exit /b 2
    )
    echo [SETUP] Dependances installees.
) ELSE (
    echo [WARN] requirements-ci.txt introuvable : %REQ%
    echo [SETUP] Installation minimale (redis + jinja2)...
    "%PIP%" install --quiet redis jinja2 matplotlib
)

:: ── Vérification finale ──────────────────────────────────────────────────────
echo.
echo [SETUP] Verification des modules critiques...
"%PYTHON%" -c "import redis; print('  redis     OK')"
"%PYTHON%" -c "import jinja2; print('  jinja2    OK')"
"%PYTHON%" -c "import matplotlib; print('  matplotlib OK')"

echo.
echo [SETUP] Environnement Python pret.
echo ====================================================
echo.

endlocal
exit /b 0

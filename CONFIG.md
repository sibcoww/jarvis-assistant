# Конфигурация путей для Jarvis Assistant

Файл `config.json` содержит пользовательские настройки приложений и сценариев.

## Переменные окружения

В путях к приложениям можно использовать переменные окружения Windows:

- `${PROGRAMFILES}` - C:\Program Files
- `${PROGRAMFILES(X86)}` - C:\Program Files (x86)
- `${APPDATA}` - C:\Users\<username>\AppData\Roaming
- `${LOCALAPPDATA}` - C:\Users\<username>\AppData\Local
- `${USERPROFILE}` - C:\Users\<username>
- `${VSCODE_PATH}` - пользовательская переменная для VS Code

## Пример настройки

1. Скопируйте `config.json.example` в `config.json`
2. Установите переменную окружения для VS Code (опционально):
   ```powershell
   [System.Environment]::SetEnvironmentVariable('VSCODE_PATH', 'E:\Tools\Microsoft VS Code\Code.exe', 'User')
   ```
3. Отредактируйте `config.json` под свои нужды

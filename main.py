import logging
from aiogram import Bot, Dispatcher, types, executor
import httpx
import json
import os
import logging
from logging import FileHandler
from typing import List, Dict, Union
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
import uuid

# Загрузка конфигурации бота из файла
with open("config.json", encoding="utf-8") as file_handler:
    CONFIG = json.load(file_handler)

# Получение токена бота из конфигурации
BOT_TOKEN = CONFIG["tg_token"]

# Список пользователей с админским доступом
users = CONFIG["admin_tg_id"]

# Имя файла для сохранения логов
# LOG_FILE = 'bot_log.txt'

# Включаем логирование, чтобы видеть ошибки
logging.basicConfig(level=logging.INFO)

# Обработчик для сохранения логов в файл
# file_handler = FileHandler(LOG_FILE)
# file_handler.setLevel(logging.INFO)
# logging.getLogger().addHandler(file_handler)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Middleware для проверки доступа пользователей
class AccessMiddleware(BaseMiddleware):
    async def on_pre_process_message(self, message: types.Message, data: dict):
        if message.from_user.id not in users:
            await message.reply("У вас нет доступа к использованию бота.")
            raise CancelHandler()

    async def on_pre_process_callback_query(self, callback_query: types.CallbackQuery, data: dict):
        if callback_query.from_user.id not in users:
            await callback_query.answer("У вас нет доступа к использованию бота.")
            raise CancelHandler()

# Добавление middleware для проверки доступа
dp.middleware.setup(AccessMiddleware())

# Данные для авторизации
AUTHORIZATION_DATA = CONFIG["key"]

# Глобальная переменная для хранения токена
access_token = None

# Функция для формирования уникального идентификатора запроса
def generate_rquid() -> str:
    return str(uuid.uuid4())

# Функция для авторизации
async def authorize() -> None:
    global access_token

    auth_url = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    headers = {
        'Authorization': f'Bearer {AUTHORIZATION_DATA}',
        'RqUID': generate_rquid(),
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {'scope': 'GIGACHAT_API_PERS'}

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(auth_url, headers=headers, data=data)

    if response.status_code == 200:
        auth_data = response.json()
        access_token = auth_data.get('access_token', '')
    else:
        print("Ошибка авторизации")

# Функция для отправки сообщений пользователя в API
async def send_user_messages(access_token: str, messages: List[Dict[str, Union[str, int]]], timeout: int = 30):
    api_url = 'https://gigachat.devices.sberbank.ru/api/v1/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}',
    }
    data = {
        'model': 'GigaChat:latest',
        'messages': messages,
        'temperature': 0.5,
    }

    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        response = await client.post(api_url, headers=headers, json=data)

    return response.json(), response.status_code

# Функция для обработки сообщений пользователя из API
async def handle_user_messages(message: types.Message, access_token: str, user_id: int):
    rquid = generate_rquid()
    messages_to_api = [{'role': 'user', 'content': message.text}]
    api_response, status_code = await send_user_messages(access_token, messages_to_api)

    if status_code == 200:
        assistant_message = api_response.get('choices', [])[0].get('message', {}).get('content', '')
        messages_out_api = [{'role': 'assistant', 'content': assistant_message}]

        save_history(user_id, messages_to_api)
        save_history(user_id, messages_out_api)

        await message.reply(assistant_message)
    elif status_code == 401:
        await authorize()
        await handle_user_messages(message, access_token, user_id)
    else:
        await message.reply("Ошибка отправки сообщения. Неизвестная ошибка.")

# Функция для сохранения истории сообщений
def save_history(user_id: int, messages: List[Dict[str, Union[str, int]]]):
    history_file = f'history_{user_id}.json'
    try:
        with open(history_file, 'r') as file:
            history = json.load(file)
    except FileNotFoundError:
        history = []

    history.extend(messages)

    with open(history_file, 'w') as file:
        json.dump(history, file)

# Обработчик команды /start
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.reply("Чем могу помочь?")

# Обработчик команды /reset
@dp.message_handler(commands=['reset'])
async def reset_command(message: types.Message):
    user_id = message.from_user.id
    history_file = f'history_{user_id}.json'

    try:
        os.remove(history_file)
        await message.reply("История чата успешно удалена.")
    except FileNotFoundError:
        await message.reply("Файл истории чата не найден.")
    except Exception as e:
        await message.reply(f"Произошла ошибка при удалении истории чата: {str(e)}")

# Обработчик всех остальных сообщений
@dp.message_handler()
async def handle_user_message(message: types.Message):
    if access_token is None:
        await authorize()  # Авторизуемся, если токен отсутствует
    user_id = message.from_user.id

    # Обрабатываем сообщения от пользователя
    await handle_user_messages(message, access_token, user_id)

# Запуск бота
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)

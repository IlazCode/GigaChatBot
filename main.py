import logging
from logging import FileHandler
import uuid
from aiogram import Bot, Dispatcher, types, executor
import httpx
import time
import json
from typing import List, Dict, Union
import os
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware


# Telegram bot's token
with open("config.json", encoding="utf-8") as file_handler:
    CONFIG = json.load(file_handler)

# Замените 'YOUR_BOT_TOKEN' на токен вашего бота
BOT_TOKEN = CONFIG["tg_token"]

users = CONFIG["admin_tg_id"]

class AccessMiddleware(BaseMiddleware):
    """
    Функция, которая вызывается перед обработкой сообщения. Она проверяет, имеет ли пользователь доступ к использованию бота, и вызывает исключение CancelHandler, если доступа нет.
    """
    async def on_pre_process_message(self, message: types.Message, data: dict):
        if message.from_user.id not in users:
            await message.reply("У вас нет доступа к использованию бота.")
            raise CancelHandler()

    """
    Функция, Когда пользователь нажимает на кнопку в чате или встроенной клавиатуре. Она проверяет, имеет ли пользователь доступ к использованию бота.
    """
    async def on_pre_process_callback_query(self, callback_query: types.CallbackQuery, data: dict):
        if callback_query.from_user.id not in users:
            await callback_query.answer("У вас нет доступа к использованию бота.")
            raise CancelHandler()

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
# Добавляем middleware к диспетчеру
dp.middleware.setup(AccessMiddleware())

# Замените 'YOUR_AUTHORIZATION_DATA' на ваши фактические авторизационные данные
AUTHORIZATION_DATA = CONFIG["key"]

# Функция для формирования уникального идентификатора запроса
def generate_rquid() -> str:
    return str(uuid.uuid4())


# Функция для выполнения запроса авторизации

async def authorize(rquid: str) -> dict:
    auth_url = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    headers = {
        'Authorization': f'Bearer {AUTHORIZATION_DATA}',
        'RqUID': rquid,
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {'scope': 'GIGACHAT_API_PERS'}

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(auth_url, headers=headers, data=data)

    return response.json(), response.status_code


# Функция для проверки срока действия токена
# def is_token_expired(expires_at: int) -> bool:
#     current_time = int(time.time() * 1000)  # Текущее время в миллисекундах
#     return expires_at < current_time


# Функция для сохранения истории сообщений в файл
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


# Функция для отправки сообщений от пользователя в API

async def send_user_messages(access_token: str, messages: List[Dict[str, Union[str, int]]]):
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

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(api_url, headers=headers, json=data)

    return response.json(), response.status_code


# Функция для обработки ответа API на отправку сообщений от пользователя
async def handle_user_messages(message: types.Message, access_token: str, user_id: int):
    # Генерируем уникальный идентификатор запроса
    rquid = generate_rquid()

    # Выполняем запрос отправки сообщений от пользователя
    messages_to_api = [
        {'role': 'user', 'content': message.text},
        {'role': 'assistant', 'content': ''},
    ]
    api_response, status_code = await send_user_messages(access_token, messages_to_api)

    if status_code == 200:
        # Забираем значение "message" из ответа API
        assistant_message = api_response.get('choices', [])[0].get('message', {}).get('content', '')

        # Сохраняем историю сообщений
        save_history(user_id, messages_to_api)

        # Отправляем ответ от API пользователю
        await message.reply(assistant_message)
    elif status_code == 401:
        error_message = api_response.get('message', '')
        if "Token has expired" in error_message:
            # Переавторизуемся в случае истечения срока действия токена
            await handle_authorization(message)
            # Повторно отправляем сообщения от пользователя
            await handle_user_messages(message, access_token, user_id)
        else:
            await message.reply("Ошибка отправки сообщения. Неверные данные авторизации.")
    else:
        await message.reply("Ошибка отправки сообщения. Неизвестная ошибка.")


# Обработка команды /start

@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.reply("Чем могу помочь?")


# Обработка команды /reset

@dp.message_handler(commands=['reset'])
async def reset_command(message: types.Message):
    # Получаем уникальный идентификатор пользователя
    user_id = message.from_user.id
    # Создаем имя файла для данного пользователя
    history_file = f'history_{user_id}.json'

    try:
        # Пытаемся удалить файл истории чата
        os.remove(history_file)
        await message.reply("История чата успешно удалена.")
    except FileNotFoundError:
        await message.reply("Файл истории чата не найден.")
    except Exception as e:
        await message.reply(f"Произошла ошибка при удалении истории чата: {str(e)}")


# Обработка сообщений от пользователя
@dp.message_handler()
async def handle_user_message(message: types.Message):
    # Получаем уникальный идентификатор пользователя
    user_id = message.from_user.id
    # Получаем токен доступа из функции авторизации
    auth_response, _ = await authorize(generate_rquid())
    access_token = auth_response.get('access_token', '')

    if not access_token:
        await message.reply("Ошибка получения токена доступа. Пожалуйста, переавторизуйтесь.")
        return

    # Обрабатываем сообщения от пользователя
    await handle_user_messages(message, access_token, user_id)


# Запуск бота
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)

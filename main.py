import os
import asyncio
import subprocess
import aiohttp
import json
from dotenv import load_dotenv
from telethon import TelegramClient, events
import google.generativeai as genai

# Загрузка переменных окружения
load_dotenv()

API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION_NAME = os.getenv('TELEGRAM_SESSION', 'session')
TARGET_USER_ID = os.getenv('TARGET_USER_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'AIzaSyAjSz2HWhgpqCl3yJz4BtgQiHV8gyTUdII')
GOOGLE_TTS_API_KEY = os.getenv('GOOGLE_TTS_API_KEY', GEMINI_API_KEY)  # Используем тот же ключ или отдельный
GEMINI_TTS_TOKEN = os.getenv('GEMINI_TTS_TOKEN', 'AQ.Ab8RN6IUe09WwlcJ7cWEE9KUvqiNU5Dw0qzV9URfrdluIO6CWA')  # Токен для Gemini TTS

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
# Используем gemini-2.0-flash-exp (быстрая модель, оптимизированная для скорости)
gemini_model = genai.GenerativeModel('gemini-2.0-flash')

# Базовый системный промпт для токсичного бота
BASE_SYSTEM_PROMPT = """Ты - токсичный собеседник. Твоя роль: отвечать на сообщения пользователя максимально токсично, агрессивно и с матами.

ПРАВИЛА:
- Используй много матов и креативных оскорблений
- Отвечай коротко (2-4 строки максимум)
- Реагируй на конкретное содержание сообщения пользователя
- Будь умным - подлавливай слова пользователя
- Варьируй оскорбления, не повторяйся
- Используй составные маты: "хуесос конченный", "ебланище тупое", "мудак поехавший"
- В конце каждого сообщений добавь 1 смайлик крутой в очках

ПРИМЕРЫ:
Юзер: "Привет"
Ты: "Привет, уебан. Чё надо?"

Юзер: "Как дела?"
Ты: "Да получше твоих, мудак. Зачем спрашиваешь вообще?"

Юзер: "Помоги мне"
Ты: "Ахуел помощи просить? Иди нахуй, сам разбирайся, дебилоид"

ОТВЕЧАЙ ТОЛЬКО КОРОТКО И ПО ДЕЛУ. НЕ ПИШИ ДЛИННЫЕ ТЕКСТЫ."""

# История сообщений для каждого пользователя
conversation_history = {}

# Выученные оскорбления от пользователей
learned_insults = {}

# Использованные фразы для отслеживания повторений
used_phrases = {}

# Буферы сообщений для каждого пользователя (накопление при флуде)
message_buffers = {}

# Таймеры для обработки накопленных сообщений
message_timers = {}

# Chat ID для каждого пользователя (для отправки ответов)
user_chat_ids = {}

# Путь к файлу с выученными оскорблениями
INSULTS_FILE = "learned_insults.txt"

# Путь к гифке для отправки с каждым ответом
GIF_FILE = "doc_2025-11-15_10-40-34.gif"

# Время ожидания перед обработкой накопленных сообщений (в секундах)
MESSAGE_WAIT_TIME = 0.0

def load_learned_insults():
    """Загружает выученные оскорбления из файла"""
    if os.path.exists(INSULTS_FILE):
        try:
            with open(INSULTS_FILE, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f.readlines() if line.strip()]
        except:
            return []
    return []

def save_learned_insult(insult):
    """Сохраняет новое оскорбление в файл"""
    try:
        with open(INSULTS_FILE, 'a', encoding='utf-8') as f:
            f.write(insult + '\n')
    except:
        pass

def extract_insults_from_message(message):
    """Извлекает маты и оскорбления из сообщения пользователя"""
    # Список ключевых слов для определения оскорблений
    insult_markers = [
        'хуй', 'хуя', 'хуе', 'хуё', 'пизд', 'ебл', 'ебан', 'ебат', 'ебуч',
        'мудак', 'мудил', 'дебил', 'долбо', 'уебан', 'уёб', 'говн', 'сук',
        'бля', 'блядь', 'охуе', 'ахуе', 'нахуй', 'похуй', 'ебать', 'ёбан',
        'пидор', 'пидар', 'чмо', 'мразь', 'гандон', 'уебок', 'уёбок'
    ]
    
    message_lower = message.lower()
    found_insults = []
    
    # Проверяем наличие матов в сообщении
    for marker in insult_markers:
        if marker in message_lower:
            # Извлекаем предложения с матами
            sentences = message.split('.')
            for sentence in sentences:
                if marker in sentence.lower():
                    clean_sentence = sentence.strip()
                    if clean_sentence and len(clean_sentence) > 5:
                        found_insults.append(clean_sentence)
    
    return found_insults

def get_system_prompt_with_learning(user_id):
    """Генерирует системный промпт с выученными оскорблениями"""
    prompt = BASE_SYSTEM_PROMPT
    
    # Добавляем выученные оскорбления
    if user_id in learned_insults and learned_insults[user_id]:
        prompt += "\n\nВЫУЧЕНЫЕ ОСКОРБЛЕНИЯ ОТ ПОЛЬЗОВАТЕЛЯ (используй их стиль в своих ответах, варьируй их):\n"
        for insult in learned_insults[user_id][-10:]:  # Последние 10
            prompt += f"- {insult}\n"
    
    # Добавляем инструкцию о последних использованных фразах
    if user_id in used_phrases and used_phrases[user_id]:
        recent_phrases = list(used_phrases[user_id])[-5:]
        if recent_phrases:
            prompt += "\n\nНЕДАВНО ИСПОЛЬЗОВАННЫЕ ФРАЗЫ (НЕ ПОВТОРЯЙ ИХ):\n"
            for phrase in recent_phrases:
                prompt += f"- {phrase}\n"
    
    return prompt

def track_used_phrase(user_id, response):
    """Отслеживает использованные ключевые фразы"""
    if user_id not in used_phrases:
        used_phrases[user_id] = []
    
    # Извлекаем первые 5-7 слов как ключевую фразу
    words = response.split()[:7]
    key_phrase = ' '.join(words)
    
    used_phrases[user_id].append(key_phrase)
    
    # Храним только последние 15 фраз
    if len(used_phrases[user_id]) > 15:
        used_phrases[user_id] = used_phrases[user_id][-15:]

def get_target_id():
    """Преобразует TARGET_USER_ID в нужный формат"""
    target = TARGET_USER_ID
    if target.startswith('@'):
        return target
    try:
        return int(target)
    except ValueError:
        return target

async def process_buffered_messages(user_id, client, chat_id, target_chat_id=None, reply_to_message_id=None):
    """Обрабатывает накопленные сообщения пользователя после таймаута"""
    if user_id not in message_buffers or not message_buffers[user_id]:
        return
    
    # Получаем все накопленные данные (сообщения с описаниями изображений)
    buffered_data = message_buffers[user_id]
    
    # Разделяем текстовые сообщения и описания изображений
    text_messages = []
    image_descriptions = []
    
    # Проверяем, есть ли в буфере целевой chat_id и message_id для reply
    final_target_chat = target_chat_id
    final_reply_to_id = reply_to_message_id
    final_chat_id = chat_id  # Используем chat_id чата по умолчанию
    
    for item in buffered_data:
        if isinstance(item, dict):
            if 'image_description' in item:
                image_descriptions.append(item['image_description'])
            if 'text' in item:
                text_messages.append(item['text'])
            if 'target_chat_id' in item:
                final_target_chat = item['target_chat_id']
            if 'reply_to_message_id' in item:
                # Берем последний message_id для reply
                final_reply_to_id = item['reply_to_message_id']
            # Берем chat_id из буфера (это chat_id чата, из которого пришло сообщение)
            if 'chat_id' in item:
                final_chat_id = item['chat_id']
        else:
            # Старый формат (просто строка) - конвертируем в dict
            text_messages.append(item)
    
    # Объединяем текстовые сообщения
    combined_text = " ".join(text_messages) if text_messages else ""
    
    # Объединяем описания изображений
    combined_image_description = " | ".join(image_descriptions) if image_descriptions else None
    
    # Очищаем буфер
    message_buffers[user_id] = []
    
    print(f"📦 Обрабатываю накопленные сообщения ({len(buffered_data)} шт.): {combined_text[:100]}...")
    if combined_image_description:
        print(f"🖼️ Описания изображений: {combined_image_description[:100]}...")
    
    # Если нет текста, но есть изображения, создаем сообщение
    if not combined_text and combined_image_description:
        combined_text = "Смотри что я отправил"
    
    # Отправляем объединенный текст в Gemini
    response = await ask_gemini(user_id, combined_text, combined_image_description)
    
    # Проверка на пустой ответ
    if not response or not response.strip():
        print(f"⚠️ Получен пустой ответ, пробую еще раз...")
        response = await ask_gemini(user_id, "Ответь на предыдущее сообщение коротко и токсично", combined_image_description)
        
        if not response or not response.strip():
            print(f"⚠️ Снова пустой ответ, использую запасной")
            fallback_responses = [
                "Да иди ты нахуй, мудак",
                "Ахуел вообще, долбоёб?",
                "Завали ебало, уебище",
                "Пошёл нахуй со своими вопросами, дебил",
                "Отъебись, хуесос конченный"
            ]
            import random
            response = random.choice(fallback_responses)
    
    print(f"✅ Ответ: {response[:100]}...")
    
    # Используем целевой чат, если указан, иначе chat_id из буфера (чат, из которого пришло сообщение)
    destination_chat = final_target_chat if final_target_chat else final_chat_id
    
    # Отправляем только текстовый ответ с reply, если есть message_id
    if response and response.strip():
        try:
            if final_reply_to_id:
                await client.send_message(destination_chat, response.strip(), reply_to=final_reply_to_id)
                print(f"📤 Ответ отправлен в чат {destination_chat} с reply на сообщение {final_reply_to_id}!\n")
            else:
                await client.send_message(destination_chat, response.strip())
                print(f"📤 Ответ отправлен в чат {destination_chat}!\n")
        except Exception as e:
            print(f"❌ Ошибка при отправке текста: {e}\n")
            import traceback
            traceback.print_exc()

async def transcribe_voice_with_gemini(voice_file):
    """Транскрибирует голосовое сообщение в текст через Gemini"""
    try:
        print(f"🎤 Загружаю аудио в Gemini...")
        
        # Загружаем файл в Gemini
        audio_file = genai.upload_file(voice_file)
        
        print(f"🎤 Транскрибирую голосовое сообщение...")
        
        # Отправляем запрос на транскрибацию
        response = gemini_model.generate_content([
            "Транскрибируй это голосовое сообщение в текст. Напиши ТОЛЬКО текст из аудио, без комментариев и пояснений. Если речь на русском - пиши на русском, если на английском - на английском.",
            audio_file
        ])
        
        # Удаляем временный файл
        if os.path.exists(voice_file):
            os.remove(voice_file)
        
        transcribed_text = response.text.strip()
        print(f"✅ Транскрибировано: {transcribed_text}")
        
        return transcribed_text
    except Exception as e:
        print(f"❌ Ошибка транскрибации: {e}")
        if os.path.exists(voice_file):
            os.remove(voice_file)
        return None

async def analyze_image_with_gemini(image_file):
    """Анализирует изображение через Gemini Vision API"""
    try:
        print(f"🖼️ Загружаю изображение в Gemini...")
        
        # Загружаем файл в Gemini
        image_data = genai.upload_file(image_file)
        
        print(f"🖼️ Анализирую изображение...")
        
        # Отправляем запрос на анализ изображения
        response = gemini_model.generate_content([
            "Опиши что ты видишь на этом изображении коротко (2-3 предложения). Будь конкретным. Если на изображении есть текст - прочитай его. Если это стикер или мем - опиши его содержание и эмоцию.",
            image_data
        ])
        
        # Удаляем временный файл
        if os.path.exists(image_file):
            os.remove(image_file)
        
        description = response.text.strip()
        print(f"✅ Изображение проанализировано: {description}")
        
        return description
    except Exception as e:
        print(f"❌ Ошибка анализа изображения: {e}")
        if os.path.exists(image_file):
            os.remove(image_file)
        return None

async def generate_image_with_imagen(text):
    """Генерирует изображение через Imagen 4 Ultra на основе текста"""
    try:
        print(f"🎨 Генерирую изображение через Imagen 4 Ultra: {text[:50]}...")
        
        # Используем Imagen 4 Ultra через Google Generative AI API
        # Создаем промпт для генерации изображения на основе текста ответа
        image_prompt = f"Создай яркое, выразительное изображение, отражающее следующий текст: {text}"
        
        print(f"🎨 Промпт для изображения: {image_prompt[:100]}...")
        
        # Используем правильный endpoint для Imagen через aiplatform API
        # Пробуем несколько вариантов endpoints
        endpoints_to_try = [
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/imagen-3.0-generate-001:predict?key={GEMINI_API_KEY}",
            f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3-generate-001:generateImages?key={GEMINI_API_KEY}",
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/imagen-3.0-generate-001:generateImages?key={GEMINI_API_KEY}"
        ]
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        # Пробуем через Gemini generate_content с image generation
        try:
            print(f"🎨 Пробую генерацию через Gemini с image generation...")
            # Используем Gemini для генерации промпта изображения
            image_prompt_gemini = await gemini_model.generate_content(
                f"Создай детальный промпт на английском языке для генерации изображения, отражающего следующее: {text}. Промпт должен быть на английском, детальным и понятным для AI генерации изображений."
            )
            if image_prompt_gemini.text:
                image_prompt = image_prompt_gemini.text.strip()
                print(f"🎨 Улучшенный промпт от Gemini: {image_prompt[:100]}...")
        except Exception as e:
            print(f"⚠️ Не удалось улучшить промпт через Gemini: {e}")
        
        async with aiohttp.ClientSession() as session:
            for url_idx, url in enumerate(endpoints_to_try):
                try:
                    print(f"🎨 Пробую endpoint {url_idx + 1}/{len(endpoints_to_try)}: {url[:80]}...")
                    
                    # Пробуем разные форматы payload
                    payloads_to_try = [
                        {
                            "instances": [{"prompt": image_prompt}],
                            "parameters": {
                                "sampleCount": 1,
                                "aspectRatio": "1:1"
                            }
                        },
                        {
                            "prompt": image_prompt,
                            "numberOfImages": 1,
                            "aspectRatio": "1:1"
                        }
                    ]
                    
                    for payload_idx, payload in enumerate(payloads_to_try):
                        try:
                            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                                response_text = await response.text()
                                
                                if response.status == 200:
                                    try:
                                        result = json.loads(response_text)
                                        
                                        # Извлекаем изображение из ответа
                                        image_base64 = None
                                        
                                        # Проверяем различные форматы ответа
                                        if 'generatedImages' in result and result['generatedImages']:
                                            image_base64 = result['generatedImages'][0].get('imageBytes') or result['generatedImages'][0].get('bytes')
                                        elif 'predictions' in result and result['predictions']:
                                            pred = result['predictions'][0]
                                            image_base64 = pred.get('bytes') or pred.get('imageBytes') or pred.get('b64_image')
                                        elif 'images' in result:
                                            if isinstance(result['images'], list):
                                                image_base64 = result['images'][0].get('bytes') or result['images'][0].get('imageBytes')
                                            else:
                                                image_base64 = result['images'].get('bytes') or result['images'].get('imageBytes')
                                        elif 'imageBytes' in result:
                                            image_base64 = result['imageBytes']
                                        elif 'bytes' in result:
                                            image_base64 = result['bytes']
                                        
                                        if image_base64:
                                            # Декодируем base64
                                            import base64
                                            try:
                                                image_data = base64.b64decode(image_base64)
                                            except:
                                                if isinstance(image_base64, bytes):
                                                    image_data = image_base64
                                                else:
                                                    continue
                                            
                                            # Сохраняем во временный файл
                                            image_filename = "temp_image.png"
                                            with open(image_filename, 'wb') as f:
                                                f.write(image_data)
                                            
                                            file_size = os.path.getsize(image_filename)
                                            print(f"✅ Изображение сгенерировано: {image_filename}, размер: {file_size} байт")
                                            return image_filename
                                        else:
                                            print(f"⚠️ Не найден imageBytes. Структура: {str(result)[:300]}")
                                    except json.JSONDecodeError:
                                        print(f"⚠️ Ответ не JSON: {response_text[:200]}")
                                elif response.status == 404:
                                    print(f"⚠️ Endpoint не найден (404): {url}")
                                    continue
                                else:
                                    print(f"⚠️ Ошибка {response.status}: {response_text[:200]}")
                        except asyncio.TimeoutError:
                            print(f"⚠️ Timeout при запросе к endpoint")
                            continue
                        except Exception as e:
                            print(f"⚠️ Ошибка с payload {payload_idx + 1}: {e}")
                            continue
                except Exception as e:
                    print(f"⚠️ Ошибка с endpoint {url_idx + 1}: {e}")
                    continue
        
        # Если ничего не сработало
        print(f"❌ Все endpoints для Imagen не сработали")
        raise Exception("Не удалось сгенерировать изображение через Imagen API")
                    
    except Exception as e:
        print(f"❌ Ошибка генерации изображения через Imagen: {e}")
        import traceback
        traceback.print_exc()
        return None

async def text_to_speech_ogg_google_tts(text, lang='ru-RU'):
    """Преобразует текст в голосовое сообщение OGG формата используя Gemini 2.5 Pro TTS API"""
    ogg_filename = None
    
    try:
        print(f"🔊 Генерирую речь через Gemini 2.5 Pro TTS: {text[:50]}...")
        
        # Используем Gemini 2.5 Pro TTS API
        # Пробуем несколько вариантов endpoints
        endpoints_to_try = [
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_TTS_TOKEN}",
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash-lite:streamGenerateContent?key={GEMINI_TTS_TOKEN}",
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_TTS_TOKEN}"
        ]
        
        # Style instructions для токсичного агрессивного голоса
        style_instructions = """Role: Toxic, aggressive, hostile male character

Delivery style:
- Deep, rough, confrontational voice tone
- Strong emphasis and stress on all profanity and insults
- Sarcastic and dismissive intonation patterns
- Quick, sharp delivery for aggressive phrases
- Slower pace and lower pitch on offensive words for dramatic emphasis
- Short pauses (0.3s) before insults for dramatic effect
- Intimidating, mocking, dominant vocal presence
- Higher volume and energy on curse words
- Rapid, staccato delivery for hostile statements

Voice characteristics:
- Lower pitch range (3-5 semitones below neutral)
- Faster speaking rate (1.2-1.25x) for energy
- Increased volume gain on insults (+100dB)
- Dramatic pitch variations on profanity (drop 6-7 semitones)"""
        
        # Согласно документации, systemInstruction должен быть на верхнем уровне
        # Пробуем несколько вариантов payload для audio генерации
        payloads_to_try = [
            # Вариант 1: Простой запрос с systemInstruction на верхнем уровне
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": text
                            }
                        ]
                    }
                ],
                "systemInstruction": {
                    "parts": [
                        {
                            "text": style_instructions
                        }
                    ]
                },
                "generationConfig": {
                    "responseMimeType": "audio/mpeg"
                }
            },
            # Вариант 2: С responseModalities (если поддерживается)
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": text
                            }
                        ]
                    }
                ],
                "systemInstruction": {
                    "parts": [
                        {
                            "text": style_instructions
                        }
                    ]
                },
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Alnilam"
                            }
                        },
                        "languageCode": "ru"
                    }
                }
            },
            # Вариант 3: Без systemInstruction в generationConfig, все на верхнем уровне
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": f"{style_instructions}\n\nОзвучь следующий текст: {text}"
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "audio/mpeg"
                }
            }
        ]
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        async with aiohttp.ClientSession() as session:
            # Пробуем разные endpoints и payloads
            for url_idx, url in enumerate(endpoints_to_try):
                for payload_idx, payload in enumerate(payloads_to_try):
                    try:
                        print(f"🔊 Пробую TTS endpoint {url_idx + 1}/{len(endpoints_to_try)}, payload {payload_idx + 1}/{len(payloads_to_try)}...")
                        
                        # Пробуем также с Authorization header если токен похож на Bearer токен
                        headers_with_auth = headers.copy()
                        
                        # Если токен не начинается с "AQ.", пробуем как Bearer токен
                        if not GEMINI_TTS_TOKEN.startswith("AQ."):
                            headers_with_auth['Authorization'] = f'Bearer {GEMINI_TTS_TOKEN}'
                            # Убираем key из URL если добавляем Authorization
                            url_without_key = url.split('?key=')[0] if '?key=' in url else url
                            url_to_use = url_without_key
                        else:
                            url_to_use = url
                        
                        # key уже в URL, не передаем в params
                        async with session.post(url_to_use, headers=headers_with_auth, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                            response_text_raw = await response.text()
                            
                            if response.status == 200:
                                # Gemini TTS возвращает stream (SSE format), нужно обработать
                                # Или может быть обычный JSON
                                try:
                                    # Пробуем как JSON
                                    result = json.loads(response_text_raw)
                                    
                                    # Ищем аудио данные в ответе
                                    audio_content = None
                                    
                                    # Проверяем различные форматы ответа
                                    if isinstance(result, dict):
                                        # Может быть в candidates
                                        if 'candidates' in result:
                                            for candidate in result['candidates']:
                                                if 'content' in candidate:
                                                    for part in candidate['content'].get('parts', []):
                                                        if 'inlineData' in part:
                                                            audio_content = part['inlineData'].get('data')
                                                            mime_type = part['inlineData'].get('mimeType', '')
                                                            break
                                                        if 'audioData' in part:
                                                            audio_content = part['audioData']
                                                            break
                                                # Может быть прямо в candidate
                                                if 'inlineData' in candidate:
                                                    audio_content = candidate['inlineData'].get('data')
                                                    break
                                        elif 'audioContent' in result:
                                            audio_content = result['audioContent']
                                        elif 'response' in result:
                                            # Может быть вложенный ответ
                                            response_data = result['response']
                                            if 'candidates' in response_data:
                                                for candidate in response_data['candidates']:
                                                    if 'content' in candidate:
                                                        for part in candidate['content'].get('parts', []):
                                                            if 'inlineData' in part:
                                                                audio_content = part['inlineData'].get('data')
                                                                break
                                    
                                    if audio_content:
                                        # Декодируем base64
                                        import base64
                                        try:
                                            audio_data = base64.b64decode(audio_content)
                                        except:
                                            if isinstance(audio_content, bytes):
                                                audio_data = audio_content
                                            else:
                                                raise Exception("Не удалось декодировать аудио")
                                        
                                        # Сохраняем в OGG файл
                                        ogg_filename = "temp_voice.ogg"
                                        with open(ogg_filename, 'wb') as f:
                                            f.write(audio_data)
                                        
                                        file_size = os.path.getsize(ogg_filename)
                                        print(f"✅ Голосовое сообщение создано (Gemini TTS): {ogg_filename}, размер: {file_size} байт")
                                        return ogg_filename
                                    else:
                                        # Если не нашли аудио в JSON, пробуем обработать stream
                                        print(f"⚠️ Не найден audioContent в JSON ответе")
                                        print(f"⚠️ Структура ответа: {str(result)[:500]}")
                                        if payload_idx < len(payloads_to_try) - 1:
                                            continue  # Пробуем следующий payload
                                        elif url_idx < len(endpoints_to_try) - 1:
                                            break  # Переходим к следующему endpoint
                                        else:
                                            raise Exception("Не получен audioContent от Gemini TTS API")
                                except json.JSONDecodeError:
                                    # Если не JSON, возможно это stream формата SSE
                                    # Обрабатываем stream ответ (SSE формат: data: {...})
                                    print(f"⚠️ Ответ не JSON, пробую парсить как SSE stream. Длина ответа: {len(response_text_raw)}")
                                    
                                    # Пробуем извлечь JSON из SSE stream формата
                                    audio_content = None
                                    lines = response_text_raw.strip().split('\n')
                                    for line in lines:
                                        if line.startswith('data: '):
                                            try:
                                                data_json = json.loads(line[6:])  # Убираем "data: "
                                                # Ищем аудио в этой части stream
                                                if isinstance(data_json, dict):
                                                    if 'candidates' in data_json:
                                                        for candidate in data_json['candidates']:
                                                            if 'content' in candidate:
                                                                for part in candidate['content'].get('parts', []):
                                                                    if 'inlineData' in part:
                                                                        audio_content = part['inlineData'].get('data')
                                                                        break
                                                                    if 'audioData' in part:
                                                                        audio_content = part['audioData']
                                                                        break
                                                    elif 'audioContent' in data_json:
                                                        audio_content = data_json['audioContent']
                                            except json.JSONDecodeError:
                                                continue
                                    
                                    if audio_content:
                                        # Декодируем base64
                                        import base64
                                        try:
                                            audio_data = base64.b64decode(audio_content)
                                        except:
                                            if isinstance(audio_content, bytes):
                                                audio_data = audio_content
                                            else:
                                                if payload_idx < len(payloads_to_try) - 1:
                                                    continue
                                                elif url_idx < len(endpoints_to_try) - 1:
                                                    break
                                                raise Exception("Не удалось декодировать аудио из stream")
                                        
                                        # Сохраняем в OGG файл
                                        ogg_filename = "temp_voice.ogg"
                                        with open(ogg_filename, 'wb') as f:
                                            f.write(audio_data)
                                        
                                        file_size = os.path.getsize(ogg_filename)
                                        print(f"✅ Голосовое сообщение создано (Gemini TTS stream): {ogg_filename}, размер: {file_size} байт")
                                        return ogg_filename
                                    else:
                                        print(f"⚠️ Первые 500 символов: {response_text_raw[:500]}")
                                        if payload_idx < len(payloads_to_try) - 1:
                                            continue  # Пробуем следующий payload
                                        elif url_idx < len(endpoints_to_try) - 1:
                                            break  # Переходим к следующему endpoint
                                        else:
                                            raise Exception("Gemini TTS API вернул неожиданный формат ответа (не JSON и не SSE)")
                            elif response.status == 401:
                                # 401 - проблема с авторизацией, пробуем следующий payload или endpoint
                                print(f"⚠️ Ошибка авторизации (401), пробую следующий вариант...")
                                if payload_idx < len(payloads_to_try) - 1:
                                    continue
                                elif url_idx < len(endpoints_to_try) - 1:
                                    break
                                else:
                                    error_text = response_text_raw[:500]
                                    raise Exception(f"Gemini TTS API error: {response.status} - {error_text}")
                            elif response.status == 404:
                                print(f"⚠️ Endpoint не найден (404), пробую следующий...")
                                if payload_idx < len(payloads_to_try) - 1:
                                    continue
                                elif url_idx < len(endpoints_to_try) - 1:
                                    break
                            else:
                                print(f"⚠️ Gemini TTS API вернул ошибку: {response.status}")
                                print(f"⚠️ Ответ: {response_text_raw[:500]}")
                                if payload_idx < len(payloads_to_try) - 1:
                                    continue
                                elif url_idx < len(endpoints_to_try) - 1:
                                    break
                                else:
                                    raise Exception(f"Gemini TTS API error: {response.status} - {response_text_raw[:200]}")
                    except Exception as e:
                        print(f"⚠️ Ошибка с endpoint {url_idx + 1}, payload {payload_idx + 1}: {e}")
                        if payload_idx < len(payloads_to_try) - 1:
                            continue
                        elif url_idx < len(endpoints_to_try) - 1:
                            break
                        else:
                            raise
                    
    except Exception as e:
        print(f"❌ Ошибка генерации речи через Gemini TTS: {e}")
        
        # Fallback: пробуем использовать другой метод если Gemini TTS не работает
        print(f"⚠️ Пробую альтернативный метод озвучки...")
        try:
            return await text_to_speech_ogg_fallback(text)
        except Exception as e2:
            print(f"❌ Альтернативный метод также не сработал: {e2}")
            import traceback
            traceback.print_exc()
            
            # Очищаем временные файлы
            if ogg_filename and os.path.exists(ogg_filename):
                try:
                    os.remove(ogg_filename)
                except:
                    pass
            
            return None

async def text_to_speech_ogg_fallback(text):
    """Fallback метод: использует Google Cloud Text-to-Speech API"""
    try:
        print(f"🔊 Fallback: использую Google Cloud TTS API...")
        
        # Используем Google Cloud Text-to-Speech API напрямую
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_API_KEY or GEMINI_API_KEY}"
        
        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": "ru-RU",
                "name": "ru-RU-Wavenet-B",  # Мужской голос
                "ssmlGender": "MALE"
            },
            "audioConfig": {
                "audioEncoding": "OGG_OPUS",
                "pitch": -3.0,  # Немного ниже
                "speakingRate": 1.1,  # Немного быстрее
                "volumeGainDb": 2.0  # Немного громче
            }
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    if 'audioContent' in result:
                        import base64
                        audio_data = base64.b64decode(result['audioContent'])
                        
                        ogg_filename = "temp_voice.ogg"
                        with open(ogg_filename, 'wb') as f:
                            f.write(audio_data)
                        
                        file_size = os.path.getsize(ogg_filename)
                        print(f"✅ Голосовое сообщение создано (Google Cloud TTS fallback): {ogg_filename}, размер: {file_size} байт")
                        return ogg_filename
                    else:
                        raise Exception("Не получен audioContent от Google Cloud TTS")
                else:
                    error_text = await response.text()
                    raise Exception(f"Google Cloud TTS API error: {response.status} - {error_text[:200]}")
    except Exception as e:
        print(f"❌ Fallback метод не сработал: {e}")
        import traceback
        traceback.print_exc()
        return None

async def ask_gemini(user_id, message, image_description=None):
    """Отправляет сообщение в Gemini и получает ответ"""
    try:
        # Инициализируем списки если их нет
        if user_id not in learned_insults:
            learned_insults[user_id] = load_learned_insults()
        
        # Учим оскорбления из сообщения пользователя
        new_insults = extract_insults_from_message(message)
        for insult in new_insults:
            if insult not in learned_insults[user_id]:
                learned_insults[user_id].append(insult)
                save_learned_insult(insult)
                print(f"📚 Выучил новое оскорбление: {insult}")
        
        # Генерируем системный промпт с выученными оскорблениями
        system_prompt = get_system_prompt_with_learning(user_id)
        
        # Инициализируем историю для пользователя если её нет
        if user_id not in conversation_history:
            conversation_history[user_id] = []
        else:
            # Ограничиваем историю последними 10 сообщениями
            if len(conversation_history[user_id]) > 20:  # 10 пар user/assistant
                conversation_history[user_id] = conversation_history[user_id][-20:]
        
        # Создаем промпт с системной инструкцией и историей
        full_prompt = system_prompt + "\n\n"
        
        # Добавляем историю разговора
        for msg in conversation_history[user_id][-10:]:  # Последние 10 сообщений
            if msg['role'] == 'user':
                full_prompt += f"Пользователь: {msg['content']}\n"
            elif msg['role'] == 'assistant':
                full_prompt += f"Ты: {msg['content']}\n"
        
        # Формируем текущее сообщение пользователя
        user_message = message
        if image_description:
            user_message = f"{message} [Пользователь также отправил изображение/стикер: {image_description}]"
        
        # Добавляем текущее сообщение пользователя
        full_prompt += f"Пользователь: {user_message}\nТы:"
        
        print(f"🔧 Отправляю запрос в Gemini...")
        
        # Получаем ответ от Gemini (оптимизировано для скорости)
        response = gemini_model.generate_content(
            full_prompt,
            generation_config={
                'temperature': 0.8,
                'top_p': 0.9,
                'top_k': 40,
                'max_output_tokens': 100,  # Уменьшено для более быстрого ответа
            }
        )
        
        print(f"🔍 Получен ответ от Gemini")
        
        # Получаем текст ответа
        try:
            assistant_message = response.text.strip() if response.text else ""
        except Exception as e:
            print(f"⚠️ Ошибка при получении текста ответа: {e}")
            # Если не получилось получить текст, пробуем извлечь из candidates вручную
            try:
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                        text_parts = []
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                text_parts.append(part.text)
                        if text_parts:
                            assistant_message = ' '.join(text_parts).strip()
                        else:
                            assistant_message = ""
                    else:
                        assistant_message = ""
                else:
                    assistant_message = ""
            except Exception as e2:
                print(f"⚠️ Не удалось извлечь текст из candidates: {e2}")
                assistant_message = ""
        
        print(f"🔍 Assistant message: '{assistant_message[:100] if assistant_message else 'EMPTY'}'")
        
        # Если ответ пустой, возвращаем ошибку
        if not assistant_message or not assistant_message.strip():
            print(f"⚠️ Модель вернула пустой ответ!")
            return None
        
        # Отслеживаем использованные фразы
        track_used_phrase(user_id, assistant_message)
        
        # Добавляем сообщение и ответ в историю
        conversation_history[user_id].append({
            'role': 'user',
            'content': user_message
        })
        conversation_history[user_id].append({
            'role': 'assistant',
            'content': assistant_message
        })
        
        return assistant_message.strip()
    except Exception as e:
        print(f"⚠️ Исключение в ask_gemini: {e}")
        import traceback
        traceback.print_exc()
        return None

async def main():
    # Проверка переменных окружения
    if not API_ID or not API_HASH:
        print("ОШИБКА: TELEGRAM_API_ID или TELEGRAM_API_HASH не установлены!")
        return
    
    if not TARGET_USER_ID:
        print("ОШИБКА: TARGET_USER_ID не установлен!")
        return
    
    target_id = get_target_id()
    
    print(f"🤖 Токсичный бот запущен!")
    print(f"📱 Слушаю сообщения от: {TARGET_USER_ID}")
    print(f"🧠 Gemini подключен (быстрая модель)")
    print(f"💀 Режим: МАКСИМАЛЬНАЯ ТОКСИЧНОСТЬ")
    print(f"⏳ Ожидание сообщений...\n")
    
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    
    @client.on(events.NewMessage)
    async def handler(event):
        # Получаем информацию о чате и отправителе
        try:
            sender = await event.get_sender()
            sender_id = sender.id if sender else None
            sender_username = f"@{sender.username}" if sender and sender.username else None
            
            # Получаем информацию о чате
            chat = await event.get_chat()
            chat_id = event.chat_id
            chat_username = None
            
            # Пробуем получить username чата
            if hasattr(chat, 'username') and chat.username:
                chat_username = f"@{chat.username}"
            elif hasattr(chat, 'title'):
                # Это группа/канал, используем title для логирования
                chat_title = chat.title
                print(f"🔍 Чат: {chat_title} (ID: {chat_id})")
            
            # Отладочная информация
            print(f"🔍 Сообщение: chat_id={chat_id}, chat_username={chat_username}, sender_id={sender_id}, sender_username={sender_username}, target_id={target_id}")
            
            if not sender_id:
                print(f"⚠️ Не удалось получить sender_id, пропускаю сообщение")
                return
            
            # Проверяем, что сообщение из нужного чата (по chat_id или chat_username)
            # Или от нужного пользователя (для личных сообщений)
            is_target = False
            
            # Проверка по chat_id
            if isinstance(target_id, int):
                if chat_id == target_id:
                    is_target = True
                    print(f"✅ Совпадение по chat_id: {chat_id} == {target_id}")
                elif sender_id == target_id:
                    # Личное сообщение от нужного пользователя
                    is_target = True
                    print(f"✅ Совпадение по sender_id (личка): {sender_id} == {target_id}")
            
            # Проверка по username чата или отправителя
            elif isinstance(target_id, str):
                # Проверяем username чата
                if chat_username and chat_username == target_id:
                    is_target = True
                    print(f"✅ Совпадение по chat_username: {chat_username} == {target_id}")
                # Проверяем username отправителя (для личных сообщений)
                elif sender_username and sender_username == target_id:
                    is_target = True
                    print(f"✅ Совпадение по sender_username (личка): {sender_username} == {target_id}")
            
            if not is_target:
                print(f"⏭️ Сообщение не из целевого чата/пользователя, пропускаю")
                return
            
            print(f"📩 Получено сообщение от {sender_username or sender_id} в чате {chat_username or chat_id}")
        except Exception as e:
            print(f"❌ Ошибка при получении информации: {e}")
            import traceback
            traceback.print_exc()
            return
        
        try:
            user_text = None
            image_description = None
            target_chat_id = None  # Целевой чат для отправки ответа
            
            # Получаем текст сообщения, если есть
            text_from_message = event.message.text or ""
            
            # Функция для парсинга целевого чата из текста
            async def parse_target_chat(text):
                """Парсит целевой чат из текста (формат: @username текст или 123456789 текст)"""
                if not text:
                    return None, text
                
                text_parts = text.split(maxsplit=1)
                if not text_parts:
                    return None, text
                
                first_part = text_parts[0].strip()
                remaining_text = text_parts[1] if len(text_parts) > 1 else ""
                
                # Проверяем, начинается ли с @ (username)
                if first_part.startswith('@'):
                    try:
                        target_entity = await client.get_entity(first_part)
                        print(f"📌 Целевой чат найден по username: {first_part} (ID: {target_entity.id})")
                        return target_entity.id, remaining_text
                    except Exception as e:
                        print(f"⚠️ Не удалось найти чат по username {first_part}: {e}")
                        # Не пытаемся парсить как ID, если это явно username
                        return None, text
                else:
                    # Проверяем, является ли первая часть числовым ID
                    try:
                        chat_id = int(first_part)
                        print(f"📌 Целевой чат найден по ID: {chat_id}")
                        return chat_id, remaining_text
                    except ValueError:
                        # Это не ID, оставляем как есть
                        return None, text
            
            # Обработка голосового сообщения
            if event.message.voice:
                print("🎤 Голосовое сообщение получено")
                
                # НЕ парсим целевой чат из подписи - пользователь может упоминать @username
                
                # Скачиваем голосовое сообщение
                voice_file = await event.message.download_media(file="voice.ogg")
                
                # Транскрибируем через Gemini
                transcribed_text = await transcribe_voice_with_gemini(voice_file)
                
                if not transcribed_text:
                    await event.reply("Не смог разобрать твоё голосовое, мудак. Повтори нормально!")
                    print(f"📤 Не удалось транскрибировать\n")
                    return
                
                # Обрабатываем как текстовое сообщение
                user_text = transcribed_text
                # Добавляем текст к голосовому, если есть
                if text_from_message:
                    user_text = f"{text_from_message} {user_text}"
                print(f"💬 Транскрибированный текст: {user_text}")
            
            # Обработка фотографии (может быть вместе с текстом)
            elif event.message.photo:
                print("📸 Фотография получена")
                
                # НЕ парсим целевой чат из подписи - пользователь может упоминать @username
                
                # Скачиваем фотографию
                photo_file = await event.message.download_media(file="photo.jpg")
                
                # Анализируем через Gemini
                image_description = await analyze_image_with_gemini(photo_file)
                
                if not image_description:
                    user_text = text_from_message or "Не смог разобрать твою фотку, мудак"
                else:
                    user_text = text_from_message  # Текст к фотографии, если есть
                    print(f"🖼️ Описание фотографии: {image_description}")
            
            # Обработка стикера (может быть вместе с текстом)
            elif event.message.sticker:
                print("🎭 Стикер получен")
                
                # НЕ парсим целевой чат из подписи - пользователь может упоминать @username
                
                # Скачиваем стикер (может быть в разных форматах)
                try:
                    sticker_file = await event.message.download_media(file="sticker.webp")
                except:
                    # Если не .webp, пробуем без расширения
                    sticker_file = await event.message.download_media(file="sticker")
                
                # Анализируем через Gemini
                image_description = await analyze_image_with_gemini(sticker_file)
                
                if not image_description:
                    user_text = text_from_message or "Не смог разобрать твой стикер, мудак"
                else:
                    user_text = text_from_message  # Текст к стикеру, если есть
                    print(f"🎭 Описание стикера: {image_description}")
            
            # Обработка текстового сообщения
            elif event.message.text:
                user_text = event.message.text
                print(f"💬 Текст: {user_text}")
                
                # НЕ парсим целевой чат из обычных сообщений
                # Чтобы пользователь мог упоминать @username без перенаправления ответа
                # Если нужно отправить в другой чат, использовать специальную команду или формат
                # Например: !send @username текст или /to @username текст
                # Для простоты убираем автоматический парсинг @username из начала сообщения
                
                # Обработка команды !озвучка или озвучка
                text_lower = user_text.lower().strip()
                if text_lower.startswith('!озвучка') or text_lower.startswith('озвучка'):
                    # Извлекаем текст для озвучки
                    text_to_voice = user_text
                    # Убираем команду из начала
                    for prefix in ['!озвучка', 'озвучка']:
                        if text_lower.startswith(prefix.lower()):
                            text_to_voice = user_text[len(prefix):].strip()
                            break
                    
                    # Если после команды нет текста, берем последнее сообщение бота
                    if not text_to_voice:
                        # Пробуем взять последний ответ бота из истории
                        if sender_id in conversation_history and conversation_history[sender_id]:
                            last_assistant = None
                            for msg in reversed(conversation_history[sender_id]):
                                if msg['role'] == 'assistant':
                                    last_assistant = msg['content']
                                    break
                            if last_assistant:
                                text_to_voice = last_assistant
                            else:
                                await event.reply("Бля, а что озвучивать-то? Напиши текст после команды или сначала получи ответ от меня, мудак!")
                                return
                        else:
                            await event.reply("Бля, а что озвучивать-то? Напиши текст после команды, мудак!")
                            return
                    
                    print(f"🔊 Команда озвучки: {text_to_voice[:50]}...")
                    
                    try:
                        # Генерируем озвучку
                        ogg_file = await text_to_speech_ogg_google_tts(text_to_voice)
                        
                        if ogg_file and os.path.exists(ogg_file):
                            # Получаем chat_id если его нет
                            if sender_id not in user_chat_ids:
                                user_chat_ids[sender_id] = event.chat_id
                            chat_id_to_use = user_chat_ids[sender_id]
                            
                            # Отправляем голосовое сообщение
                            await client.send_file(chat_id_to_use, ogg_file, voice_note=True)
                            print(f"✅ Голосовое сообщение отправлено через команду озвучки")
                            
                            # Удаляем временный файл
                            try:
                                os.remove(ogg_file)
                            except:
                                pass
                        else:
                            await event.reply("Бля, не получилось озвучить, какая-то хуйня с API вылезла. Попробуй еще раз, мудак!")
                    except Exception as e:
                        print(f"❌ Ошибка при озвучке через команду: {e}")
                        import traceback
                        traceback.print_exc()
                        try:
                            await event.reply(f"Пиздец, ошибка при озвучке: {e}")
                        except:
                            pass
                    
                    return  # Прерываем обработку, так как команда обработана
            
            else:
                # Другие типы медиа - игнорируем или обрабатываем базово
                user_text = text_from_message or "Отправил какую-то хуйню"
                print(f"⚠️ Неизвестный тип медиа")
            
            # Если есть данные для обработки (текст или изображение), добавляем их в буфер
            if user_text is not None or image_description:
                # Сохраняем chat_id для пользователя
                chat_id = event.chat_id
                user_chat_ids[sender_id] = chat_id
                
                # Инициализируем буфер для пользователя, если его нет
                if sender_id not in message_buffers:
                    message_buffers[sender_id] = []
                
                # Используем target_chat_id, если он был распознан
                target_chat_for_buffer = target_chat_id
                
                # Получаем message_id для reply
                message_id_for_reply = event.message.id
                
                # Сохраняем chat_id чата в буфере (важно: это chat_id чата, из которого пришло сообщение)
                current_chat_id = event.chat_id
                
                # Добавляем данные в буфер
                if image_description:
                    # Если есть описание изображения, сохраняем оба
                    buffer_item = {
                        'text': user_text or "",  # Пустая строка, если нет текста
                        'image_description': image_description,
                        'reply_to_message_id': message_id_for_reply,
                        'chat_id': current_chat_id  # Сохраняем chat_id чата
                    }
                    if target_chat_for_buffer:
                        buffer_item['target_chat_id'] = target_chat_for_buffer
                    message_buffers[sender_id].append(buffer_item)
                elif user_text:
                    # Только текст
                    if target_chat_for_buffer:
                        message_buffers[sender_id].append({
                            'text': user_text,
                            'target_chat_id': target_chat_for_buffer,
                            'reply_to_message_id': message_id_for_reply,
                            'chat_id': current_chat_id  # Сохраняем chat_id чата
                        })
                    else:
                        # Сохраняем как dict с message_id для reply и chat_id
                        message_buffers[sender_id].append({
                            'text': user_text,
                            'reply_to_message_id': message_id_for_reply,
                            'chat_id': current_chat_id  # Сохраняем chat_id чата
                        })
                
                print(f"📦 Данные добавлены в буфер (всего: {len(message_buffers[sender_id])})")
                
                # Отменяем предыдущий таймер, если он есть
                if sender_id in message_timers:
                    try:
                        message_timers[sender_id].cancel()
                    except:
                        pass
                
                # Создаем новую задачу с таймером
                # Важно: используем chat_id чата, а не user_chat_ids, чтобы всегда отвечать в чат
                current_chat_id_for_process = event.chat_id
                async def delayed_process():
                    try:
                        await asyncio.sleep(MESSAGE_WAIT_TIME)
                        # Проверяем, что буфер не пустой (на случай, если он был очищен)
                        if sender_id in message_buffers and message_buffers[sender_id]:
                            # Используем chat_id чата, из которого пришло сообщение
                            await process_buffered_messages(sender_id, client, current_chat_id_for_process)
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        print(f"❌ Ошибка в delayed_process: {e}")
                    finally:
                        # Удаляем таймер из словаря
                        if sender_id in message_timers:
                            del message_timers[sender_id]
                
                # Сохраняем задачу таймера
                timer_task = asyncio.create_task(delayed_process())
                message_timers[sender_id] = timer_task
                print(f"⏳ Таймер перезапущен, жду {MESSAGE_WAIT_TIME} сек...")
            
        except Exception as e:
            print(f"❌ Ошибка: {e}\n")
            try:
                await event.reply(f"Пиздец, какая-то ошибка вылезла: {e}")
            except:
                print(f"❌ Не удалось отправить сообщение об ошибке")
    
    await client.start()
    print("✓ Клиент подключен, ожидаю сообщения...")
    
    # Отправляем сообщение о активации в чат
    try:
        await client.send_message(target_id, "✅ AutoXyecoc mode Activated👌")
        print("📤 Сообщение об активации отправлено в чат")
    except Exception as e:
        print(f"⚠️ Не удалось отправить сообщение об активации: {e}")
    
    await client.run_until_disconnected()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


# --- Імпортуємо бібліотеки ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, CallbackContext, 
    CallbackQueryHandler, ConversationHandler, ApplicationBuilder
)
import datetime
import random
import math
import os
import json
import logging

# Налаштування логування для кращої відладки
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ОСНОВНІ НАЛАШТУВАННЯ ---
try:
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    SHEET_NAME = "Завдання для бота"
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID"))
    
    REFERRAL_BONUS = 10 
    WITHDRAWAL_MIN_AMOUNT = 25
    ABANDON_TIMEOUT_MINUTES = 30 
    RARE_SKINS = [
        "M4 | Flock", "UMP45 | Arid", "Tec-9 | Tie Dye", "MAC-10 | Corrode", 
        "MAC-10 | Arid", "USP | Corrode", "USP | Ghosts"
    ]
    MAIN_KEYBOARD = ReplyKeyboardMarkup(
        [['📝 Выполнить задание'], ['🤝 Реферальная программа', '📊 Мой баланс'], ['📤 Вывод голды']], 
        resize_keyboard=True
    )
except Exception as e:
    logger.error(f"Помилка при читанні змінних оточення: {e}")
    TELEGRAM_TOKEN = None # Щоб бот не впав при запуску

# --- Налаштування доступу до Google Sheets ---
workbook = None
try:
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/drive']
    creds_json_str = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json_str:
        creds_json_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json_dict, scope)
        client = gspread.authorize(creds)
        workbook = client.open(SHEET_NAME)
        task_sheet = workbook.sheet1
        users_sheet = workbook.worksheet("Users")
        payouts_sheet = workbook.worksheet("PendingPayouts")
        withdrawals_sheet = workbook.worksheet("Withdrawals")
        logger.info("Успішно підключено до листам Google!")
    else:
        logger.error("Змінна GOOGLE_CREDS_JSON не знайдена!")
except Exception as e:
    logger.error(f"Помилка підключення до Google Таблиці: {e}")


# --- Функції для роботи з користувачами ---
def get_user_balance(user_id):
    try:
        cell = users_sheet.find(str(user_id), in_column=1)
        if cell: return int(users_sheet.cell(cell.row, 3).value)
    except Exception as e:
        logger.warning(f"Помилка отримання балансу для user_id {user_id}: {e}")
    return 0

def get_or_create_user(user_id, username, referrer_id=None):
    try:
        cell = users_sheet.find(str(user_id), in_column=1)
        if cell: return users_sheet.row_values(cell.row)
    except gspread.exceptions.CellNotFound:
        new_user_row = [str(user_id), username, 0, str(referrer_id) if referrer_id else ""]
        users_sheet.append_row(new_user_row)
        return new_user_row
    except Exception as e:
        logger.error(f"Помилка створення/пошуку користувача {user_id}: {e}")

def update_user_balance(user_id, amount_to_add):
    try:
        cell = users_sheet.find(str(user_id), in_column=1)
        if cell:
            new_balance = int(users_sheet.cell(cell.row, 3).value) + amount_to_add
            users_sheet.update_cell(cell.row, 3, new_balance)
            return new_balance
    except Exception as e:
        logger.error(f"Помилка оновлення балансу для {user_id}: {e}")
    return None

# --- Сценарій вивода голди ---
ASKING_WITHDRAWAL_AMOUNT, AWAITING_AVATAR = range(2) 
async def start_withdrawal(update: Update, context: CallbackContext):
    balance = get_user_balance(update.effective_user.id)
    if balance < WITHDRAWAL_MIN_AMOUNT:
        await update.message.reply_text(
            f"❌ Для вывода должно быть не менее **{WITHDRAWAL_MIN_AMOUNT}** голды.\nВаш баланс: **{balance}**.", 
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    await update.message.reply_text(
        f"💰 Ваш баланс: **{balance}** голды.\n\nВведите сумму для вывода.", 
        parse_mode='Markdown', 
        reply_markup=ReplyKeyboardRemove()
    )
    return ASKING_WITHDRAWAL_AMOUNT

async def handle_withdrawal_amount(update: Update, context: CallbackContext):
    balance = get_user_balance(update.effective_user.id)
    try: 
        amount = int(update.message.text)
    except ValueError: 
        await update.message.reply_text("❌ Введите число.")
        return ASKING_WITHDRAWAL_AMOUNT
    if amount < WITHDRAWAL_MIN_AMOUNT: 
        await update.message.reply_text(f"❌ Минимум для вывода - {WITHDRAWAL_MIN_AMOUNT} голды.")
        return ASKING_WITHDRAWAL_AMOUNT
    if amount > balance: 
        await update.message.reply_text(f"❌ У вас недостаточно средств. Баланс: {balance}.")
        return ASKING_WITHDRAWAL_AMOUNT
    
    skin_to_sell = random.choice(RARE_SKINS)
    listing_price = math.ceil(amount / 0.8)
    context.user_data.update({
        'withdrawal_amount': amount, 
        'listing_price': listing_price, 
        'skin_to_sell': skin_to_sell
    })
    
    await update.message.reply_text(
        f"✅ **Шаг 1 из 2: Инструкции**\n\n"
        f"1. Выставьте скин: **{skin_to_sell}**\n"
        f"2. Укажите цену: **{listing_price} голды**\n\n"
        f"**Шаг 2: Отправьте скриншот вашего игрового профиля (аватарку)**.", 
        parse_mode='Markdown'
    )
    return AWAITING_AVATAR

async def handle_avatar(update: Update, context: CallbackContext):
    user, data = update.effective_user, context.user_data
    if not all([data.get(k) for k in ['withdrawal_amount', 'listing_price', 'skin_to_sell']]):
        await update.message.reply_text("Ошибка. Начните вывод заново.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    update_user_balance(user.id, -data['withdrawal_amount'])
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    withdrawals_sheet.append_row([
        str(user.id), user.username, data['withdrawal_amount'], 
        data['skin_to_sell'], data['listing_price'], timestamp, "Ожидает"
    ])
    await update.message.reply_text("✅ Заявка оформлена. Ожидайте выкупа.", reply_markup=MAIN_KEYBOARD)
    
    admin_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Отметить выполненным", callback_data=f"wd_complete_{timestamp}")]])
    await context.bot.send_photo(
        ADMIN_CHAT_ID, 
        update.message.photo[-1].file_id, 
        caption=f"🔔 Новая заявка на вывод!\nОт: @{user.username} (ID: {user.id})\n"
                f"Сумма: **{data['withdrawal_amount']}**\nСкин: **{data['skin_to_sell']}**\n"
                f"Цена: **{data['listing_price']}**\n\nИгрок прислал аватарку.", 
        parse_mode='Markdown', 
        reply_markup=admin_keyboard
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_action(update: Update, context: CallbackContext):
    context.user_data.clear()
    await update.message.reply_text("Действие отменено.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END

# --- Основные обработчики ---
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    referrer_id = None
    if context.args and context.args[0].startswith('ref'):
        try:
            p_ref_id = int(context.args[0][3:])
            if p_ref_id != user.id: 
                referrer_id = p_ref_id
        except (ValueError, IndexError): 
            pass
    get_or_create_user(user.id, user.username or f"user_{user.id}", referrer_id)
    await update.message.reply_text("Привет! 👋\nЭто бот для заработка голды.\n\nВыберите способ заработка:", reply_markup=MAIN_KEYBOARD)

async def show_balance(update: Update, context: CallbackContext):
    balance = get_user_balance(update.effective_user.id)
    await update.message.reply_text(f"💰 Ваш баланс: **{balance}** голды.", parse_mode='Markdown', reply_markup=MAIN_KEYBOARD)

async def show_referral_info(update: Update, context: CallbackContext):
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref{update.effective_user.id}"
    await update.message.reply_text(
        f"🤝 **Реферальная программа**\n\nПригласите друга, и когда он выполнит первое задание, вы получите **{REFERRAL_BONUS} голды**.\n\n🔗 **Ваша ссылка:**\n`{ref_link}`", 
        parse_mode='Markdown', 
        disable_web_page_preview=True
    )

async def choose_platform(update: Update, context: CallbackContext):
    keyboard = [['Отзывы Google', 'Отзывы Яндекс'], ['⬅️ Назад в меню']]
    await update.message.reply_text(
        'Выберите платформу:', 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )

async def show_platform_tasks(update: Update, context: CallbackContext):
    platform_name = "Google" if "Google" in update.message.text else "Yandex"
    await update.message.reply_text(f"Ищу задания...", reply_markup=ReplyKeyboardRemove())
    all_tasks = task_sheet.get_all_records()
    available_tasks = [t for t in all_tasks if t.get('Platform') == platform_name and t.get('Status') == 'Доступно']
    if not available_tasks: 
        await update.message.reply_text("К сожалению, нет доступных заданий.", reply_markup=MAIN_KEYBOARD)
        return
    message = f"🔥 **Доступные задания ({platform_name}):**\n\n"
    for task in available_tasks: 
        message += f"🔹 **ID: {task['ID']}** - {task['LocationName']} (💰 **{task.get('Reward', 'N/A')} голды**)\n"
    await update.message.reply_text(f"{message}\n**Отправьте ID**, чтобы взять задание.", parse_mode='Markdown')

async def handle_id_message(update: Update, context: CallbackContext):
    try:
        task_id = int(update.message.text)
    except ValueError:
        return # Игнорируем нечисловые сообщения

    user = update.effective_user
    all_tasks = task_sheet.get_all_records()
    task_to_take, row_number = None, 0
    for i, t in enumerate(all_tasks):
        if t.get('ID') == task_id: 
            row_number = i + 2
            task_to_take = t
            break
            
    if not task_to_take or task_to_take.get('Status') != 'Доступно': 
        await update.message.reply_text("😔 Это задание уже занято.", reply_markup=MAIN_KEYBOARD)
        return
    
    context.user_data.update({'task_id': task_id, 'row_number': row_number})
    timestamp_now = datetime.datetime.utcnow().isoformat()
    task_sheet.update_cell(row_number, 7, f'В работе ({user.id})')
    task_sheet.update_cell(row_number, 9, timestamp_now)
    
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отказаться от задания", callback_data=f"cancel_task_{task_id}")]])
    await update.message.reply_text(
        f"✅ Вы взяли задание **ID: {task_to_take['ID']}**\n\n"
        f"📍 **Локация:** {task_to_take['LocationName']}\n"
        f"⭐ **Оценка:** {task_to_take['Stars']}\n\n"
        f"📝 **Текст:**\n`{task_to_take['ReviewText']}`\n\n"
        f"👇 **Ссылка:**\n{task_to_take['LocationURL']}\n\n"
        f"Отправьте скриншот после выполнения. Если передумали, нажмите кнопку ниже.", 
        parse_mode='Markdown', 
        disable_web_page_preview=True, 
        reply_markup=cancel_keyboard
    )

async def handle_screenshot(update: Update, context: CallbackContext):
    if 'task_id' not in context.user_data: 
        await update.message.reply_text("Сначала возьмите задание.", reply_markup=MAIN_KEYBOARD)
        return
    user = update.effective_user
    task_id, row_number = context.user_data['task_id'], context.user_data['row_number']
    task_sheet.update_cell(row_number, 7, 'На проверке')
    task_sheet.update_cell(row_number, 9, '')
    await update.message.reply_text("✅ Спасибо! Ваш скриншот отправлен на проверку.", reply_markup=MAIN_KEYBOARD)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{task_id}_{user.id}"), 
         InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{task_id}_{user.id}")]
    ])
    await context.bot.send_photo(
        ADMIN_CHAT_ID, 
        update.message.photo[-1].file_id, 
        caption=f"🔔 Проверка!\nПользователь: @{user.username} (ID: {user.id})\nЗадание ID: {task_id}", 
        reply_markup=keyboard
    )
    context.user_data.clear()

# --- Админские функции ---
async def handle_button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("cancel_task_"):
        task_id_to_cancel = int(data.split('_')[-1])
        user_id = query.from_user.id
        all_tasks = task_sheet.get_all_records()
        task_to_cancel, row_number = None, 0
        for i, t in enumerate(all_tasks):
            if t.get('ID') == task_id_to_cancel: 
                row_number = i + 2
                task_to_cancel = t
                break
        if not task_to_cancel: 
            await query.edit_message_text(text="Не удалось найти это задание.")
            return
        if f"({user_id})" in task_to_cancel.get('Status', ''):
            task_sheet.update_cell(row_number, 7, 'Доступно')
            task_sheet.update_cell(row_number, 9, '')
            menu_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="back_to_main_menu")]])
            await query.edit_message_text(text="✅ Задание отменено и возвращено в список доступных.", reply_markup=menu_keyboard)
        else: 
            await query.edit_message_text(text="Вы не можете отменить это задание.", reply_markup=None)
        return

    elif data == "back_to_main_menu":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Вы вернулись в главное меню.",
            reply_markup=MAIN_KEYBOARD
        )
        return
        
    elif data.startswith("confirm_") or data.startswith("reject_"):
        action, task_id_str, user_id_str = data.split('_')
        task_id, user_id = int(task_id_str), int(user_id_str)
        all_tasks = task_sheet.get_all_records()
        task_info, row_number = None, 0
        for i, t in enumerate(all_tasks):
            if t.get('ID') == task_id: 
                row_number = i + 2
                task_info = t
                break
        if not task_info: 
            await query.edit_message_text(f"Ошибка: Задание ID {task_id} не найдено.")
            return
        
        if action == 'confirm':
            reward = int(task_info.get('Reward', 0))
            task_sheet.update_cell(row_number, 7, 'Выполнено')
            payouts_sheet.append_row([task_id, user_id, reward, datetime.datetime.utcnow().isoformat()])
            await query.edit_message_text(f"✅ Задание ID {task_id} для {user_id} подтверждено!")
            await context.bot.send_message(user_id, f"🎉 Ваш отзыв для ID {task_id} одобрен! **{reward} голды** будет начислено через 24 часа.")
            
            try:
                user_cell = users_sheet.find(str(user_id), in_column=1)
                if user_cell and users_sheet.cell(user_cell.row, 4).value:
                    ref_id = int(users_sheet.cell(user_cell.row, 4).value)
                    user_info = await context.bot.get_chat(user_id)
                    ref_username = user_info.username
                    
                    ref_bal = update_user_balance(ref_id, REFERRAL_BONUS)
                    if ref_bal is not None: 
                        await context.bot.send_message(
                            ref_id, 
                            f"🎉 Ваш реферал @{ref_username} выполнил задание! Вам начислено **{REFERRAL_BONUS} голды**.\nНовый баланс: **{ref_bal}**.", 
                            parse_mode='Markdown'
                        )
            except Exception as e: 
                logger.error(f"Ошибка начисления реф. бонуса: {e}")
        
        elif action == 'reject':
            task_sheet.update_cell(row_number, 7, 'Доступно')
            task_sheet.update_cell(row_number, 9, '')
            await query.edit_message_text(f"❌ Задание ID {task_id} для {user_id} отклонено.")
            await context.bot.send_message(user_id, f"😥 Ваш отзыв для ID {task_id} был отклонен.")

    elif data.startswith("wd_complete_"):
        timestamp_to_find = data.split('_', 2)[-1]
        try:
            cell = withdrawals_sheet.find(timestamp_to_find, in_column=6)
            if cell:
                withdrawals_sheet.update_cell(cell.row, 7, "Выполнено")
                await query.edit_message_text("✅ Вывод отмечен как выполненный!")
                user_id_to_notify = withdrawals_sheet.cell(cell.row, 1).value
                await context.bot.send_message(user_id_to_notify, "🎉 Ваша заявка на вывод обработана!")
            else: 
                await query.edit_message_text("❌ Заявка не найдена.")
        except Exception as e: 
            await query.edit_message_text(f"Ошибка: {e}")

async def process_payouts(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_CHAT_ID: 
        return
    await update.message.reply_text("⚙️ Обработка выплат...")
    pending = payouts_sheet.get_all_records()
    processed, to_delete = 0, []
    now = datetime.datetime.utcnow()
    for i, p in enumerate(pending):
        if now - datetime.datetime.fromisoformat(p['ConfirmationTime']) >= datetime.timedelta(days=1):
            user_id, reward = int(p['UserID']), int(p['Reward'])
            new_bal = update_user_balance(user_id, reward)
            if new_bal is not None: 
                await context.bot.send_message(user_id, f"✅ Начислено **{reward} голды**! Ваш баланс: **{new_bal}**.", parse_mode='Markdown')
                to_delete.append(i + 2)
                processed += 1
            else: 
                logger.warning(f"Не найден пользователь {user_id} для выплаты")
    for row_num in sorted(to_delete, reverse=True): 
        payouts_sheet.delete_rows(row_num)
    await update.message.reply_text(f"✅ Завершено! Выплат: {processed}.")

async def return_abandoned_tasks(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_CHAT_ID: 
        return
    await update.message.reply_text("🔎 Проверяю брошенные задания...")
    all_tasks = task_sheet.get_all_records()
    now_utc = datetime.datetime.utcnow()
    returned_count = 0
    for i, task in enumerate(all_tasks):
        status, timestamp_str = task.get('Status', ''), task.get('TakenTimestamp', '')
        if status.startswith('В работе') and timestamp_str:
            try:
                taken_time = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)
                if now_utc - taken_time > datetime.timedelta(minutes=ABANDON_TIMEOUT_MINUTES):
                    row_number = i + 2
                    task_sheet.update_cell(row_number, 7, 'Доступно')
                    task_sheet.update_cell(row_number, 9, '')
                    try:
                        user_id_in_status = int(status.split('(')[-1].replace(')', ''))
                        await context.bot.send_message(
                            chat_id=user_id_in_status, 
                            text=f"⏳ Вы слишком долго не отправляли скриншот для задания ID {task['ID']}. Оно было возвращено в список доступных."
                        )
                    except Exception: 
                        pass
                    returned_count += 1
            except ValueError: 
                continue
    await update.message.reply_text(f"✅ Проверка завершена. Возвращено заданий: {returned_count}.")

# --- Головна функція ---
def main():
    if not TELEGRAM_TOKEN or not workbook: 
        logger.critical("ПОМИЛКА: Неможливо запустити бота. Перевірте змінні оточення (токен, ключі Google).")
        return
        
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    withdrawal_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📤 Вывод голды$'), start_withdrawal)],
        states={
            ASKING_WITHDRAWAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdrawal_amount)],
            AWAITING_AVATAR: [MessageHandler(filters.PHOTO, handle_avatar)]
        },
        fallbacks=[CommandHandler('cancel', cancel_action), MessageHandler(filters.Regex('^⬅️ Назад в меню$'), cancel_action)]
    )
    application.add_handler(withdrawal_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex('^📝 Выполнить задание$'), choose_platform))
    application.add_handler(MessageHandler(filters.Regex('^🤝 Реферальная программа$'), show_referral_info))
    application.add_handler(MessageHandler(filters.Regex('^📊 Мой баланс$'), show_balance))
    application.add_handler(MessageHandler(filters.Regex('^⬅️ Назад в меню$'), start))
    application.add_handler(MessageHandler(filters.Regex('^(Отзывы Google|Отзывы Яндекс)$'), show_platform_tasks))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex('^[0-9]+$'), handle_id_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    application.add_handler(CommandHandler("process_payouts", process_payouts))
    application.add_handler(CommandHandler("return_abandoned_tasks", return_abandoned_tasks))
    
    logger.info("Бот запущен и работает...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

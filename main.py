import logging
import re
import json
import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler
)
import database

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Load configuration from JSON file
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error("config.json not found! Please create the file.")
    exit()

# Extract constants from the config
BOT_API_TOKEN = config['bot']['api_token']
ADMIN_USER_ID = config['bot']['admin_user_id']
PAYMENT_CONFIRMATION_CHANNEL_ID = config['bot']['payment_confirmation_channel_id']
EXPIRY_NOTIFICATION_GROUP_ID = config['bot']['expiry_notification_group_id']
COURSE_OPTIONS = [course['name'] for course in config['courses']]
BANK_DETAILS = (
    f"Bank Name: {config['bank_details']['name']}\n"
    f"Account Holder: {config['bank_details']['account_holder']}\n"
    f"Account Number: {config['bank_details']['account_number']}"
)

# Conversation states for enrollment
(
    ASKING_FULL_NAME,
    ASKING_PHONE_NUMBER,
    ASKING_COURSE_SELECTION,
    WAITING_FOR_RECEIPT
) = range(4)

# Conversation states for certificate
(
    ASKING_CERTIFICATE_CONFIRMATION,
    WAITING_FOR_CERTIFICATE_RECEIPT
) = range(4, 6)


# Ethiopian phone number regex
ETHIOPIAN_PHONE_REGEX = r'^(\+251|0)?(9|7)[0-9]{8}$'


# --- Helper Functions ---

def get_course_details(course_name):
    """Finds course details from config based on course name."""
    for course in config['courses']:
        if course['name'] == course_name:
            return course
    return None

async def check_expiry_and_notify(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A scheduled job to check for expired enrollments and certificate eligibility."""
    logger.info("Running daily check for student statuses...")
    
    students = database.get_verified_students_for_job()
    
    for student in students:
        user_id, chat_id, course_name, verification_date_str = student
        
        course_details = get_course_details(course_name)
        if not course_details:
            logger.error(f"Course details not found for {course_name} for student {user_id}")
            continue

        verification_date = datetime.datetime.strptime(verification_date_str, '%Y-%m-%d %H:%M:%S.%f')
        
        # Check for course expiry
        expiry_date = verification_date + datetime.timedelta(days=course_details['duration_days'])
        if datetime.datetime.now() > expiry_date:
            logger.info(f"Student {user_id} course has expired. Removing from group and notifying.")
            
            student_info = database.get_student_info(user_id)
            if student_info:
                _, _, full_name, _, _, _, _, _, _, _, _, _ = student_info
                try:
                    await context.bot.ban_chat_member(chat_id=course_details['group_id'], user_id=user_id)
                    await context.bot.unban_chat_member(chat_id=course_details['group_id'], user_id=user_id)
                except Exception as e:
                    logger.error(f"Failed to remove student {user_id} from group: {e}")

                database.update_payment_status_to_expired(user_id)

                message = config['messages']['course_expiry_student'].format(
                    user_mention=f'<a href="tg://user?id={user_id}">{full_name}</a>',
                    course_name=course_name
                )
                re_enroll_button = InlineKeyboardButton("Re-enroll", callback_data="re_enroll")
                re_enroll_keyboard = InlineKeyboardMarkup([[re_enroll_button]])
                
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        reply_markup=re_enroll_keyboard,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Failed to send expiry message to student {user_id}: {e}")

                admin_message = config['messages']['course_expiry_admin'].format(
                    user_name=full_name,
                    user_id=user_id,
                    course_name=course_name
                )
                try:
                    await context.bot.send_message(
                        chat_id=EXPIRY_NOTIFICATION_GROUP_ID,
                        text=admin_message
                    )
                except Exception as e:
                    logger.error(f"Failed to send expiry notification to admin channel: {e}")


        # Check for certificate eligibility
        cert_eligible_date = verification_date + datetime.timedelta(days=course_details['certificate_wait_days'])
        student_info = database.get_student_info(user_id)
        if student_info:
            _, _, full_name, _, _, _, _, _, _, cert_status, _, notified = student_info
            if datetime.datetime.now() > cert_eligible_date and not notified and cert_status == 'none':
                logger.info(f"Student {user_id} is now eligible for certificate. Notifying.")
                
                message = config['messages']['certificate_eligibility'].format(
                    user_mention=f'<a href="tg://user?id={user_id}">{full_name}</a>',
                    course_name=course_name,
                    certificate_price=course_details['certificate_price']
                )
                cert_button = InlineKeyboardButton("Apply for Certificate", callback_data="start_certificate")
                cert_keyboard = InlineKeyboardMarkup([[cert_button]])
                
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        reply_markup=cert_keyboard,
                        parse_mode='HTML'
                    )
                    database.update_certificate_notified(user_id)
                except Exception as e:
                    logger.error(f"Failed to send certificate eligibility message to student {user_id}: {e}")

# --- Command Handlers for Enrollment Conversation ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the enrollment conversation."""
    user = update.effective_user
    welcome_message = config['messages']['welcome'].format(user_mention=user.mention_html())
    await update.message.reply_html(welcome_message)
    context.user_data['telegram_user_id'] = user.id
    context.user_data['chat_id'] = update.effective_chat.id
    return ASKING_FULL_NAME

async def get_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the full name and asks for the phone number."""
    full_name = update.message.text
    context.user_data['full_name'] = full_name
    await update.message.reply_text(config['messages']['ask_phone'])
    return ASKING_PHONE_NUMBER

async def get_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validates the phone number and asks for course selection."""
    phone_number = update.message.text
    if re.fullmatch(ETHIOPIAN_PHONE_REGEX, phone_number):
        context.user_data['phone_number'] = phone_number
        
        keyboard = [[course] for course in COURSE_OPTIONS]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            config['messages']['ask_course'],
            reply_markup=reply_markup
        )
        return ASKING_COURSE_SELECTION
    else:
        await update.message.reply_text(config['messages']['invalid_phone'])
        return ASKING_PHONE_NUMBER

async def get_course_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the course selection and provides payment instructions."""
    course_selection = update.message.text
    if course_selection in COURSE_OPTIONS:
        context.user_data['course_selected'] = course_selection
        
        selected_course = get_course_details(course_selection)
        course_price = selected_course['price'] if selected_course else "N/A"
        
        payment_message = config['messages']['payment_instructions'].format(
            course_name=course_selection,
            course_price=course_price,
            bank_details=BANK_DETAILS
        )
        
        await update.message.reply_text(payment_message, reply_markup=ReplyKeyboardRemove())
        return WAITING_FOR_RECEIPT
    else:
        await update.message.reply_text("Please use the buttons provided to select a course.")
        return ASKING_COURSE_SELECTION

async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the received payment receipt, saves data, and notifies the admin."""
    user_data = context.user_data
    user_id = user_data.get('telegram_user_id')
    
    photo_file = update.message.photo[-1]
    image_bytes = await photo_file.get_file().download_as_bytearray()
    
    database.add_student(
        user_id=user_id,
        full_name=user_data.get('full_name'),
        phone_number=user_data.get('phone_number'),
        course_selected=user_data.get('course_selected'),
        payment_receipt_image=image_bytes,
        chat_id=user_data.get('chat_id')
    )
    
    await update.message.reply_text(
        config['messages']['enrollment_success'],
        reply_markup=ReplyKeyboardRemove()
    )

    caption = config['messages']['admin_notification'].format(
        user_id=user_id,
        user_name=user_data.get('full_name'),
        phone_number=user_data.get('phone_number'),
        course_name=user_data.get('course_selected')
    )
    
    try:
        await context.bot.send_photo(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            photo=image_bytes,
            caption=caption
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification: {e}")
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"ERROR: Could not send enrollment notification to channel. Details for student {user_id}:\n{caption}"
        )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ends the conversation gracefully."""
    await update.message.reply_text(
        config['messages']['cancel'],
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# --- Command Handlers for Certificate Conversation ---

async def certificate_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the certificate application process."""
    user = update.effective_user
    student_info = database.get_student_info(user.id)
    
    if not student_info or student_info[6] != 'verified':
        await update.message.reply_text("You must be an enrolled student to apply for a certificate.")
        return ConversationHandler.END

    _, _, _, _, _, _, _, _, verification_date_str, _, _, _ = student_info
    course_name = student_info[4]
    course_details = get_course_details(course_name)

    if not course_details:
        await update.message.reply_text("Course details not found. Please contact support.")
        return ConversationHandler.END

    verification_date = datetime.datetime.strptime(verification_date_str, '%Y-%m-%d %H:%M:%S.%f')
    wait_days = course_details['certificate_wait_days']
    cert_eligible_date = verification_date + datetime.timedelta(days=wait_days)

    if datetime.datetime.now() < cert_eligible_date:
        days_left = (cert_eligible_date - datetime.datetime.now()).days + 1
        await update.message.reply_text(
            f"You are not yet eligible to apply for a certificate. Please wait {days_left} more day(s) after your course verification."
        )
        return ConversationHandler.END

    cert_price = course_details['certificate_price']
    message = config['messages']['certificate_ask'].format(
        course_name=course_name,
        certificate_price=cert_price,
        bank_details=BANK_DETAILS
    )
    
    context.user_data['course_name'] = course_name
    
    await update.message.reply_text(message)
    return WAITING_FOR_CERTIFICATE_RECEIPT


async def receive_certificate_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the received certificate payment receipt."""
    user_data = context.user_data
    user_id = update.effective_user.id
    
    photo_file = update.message.photo[-1]
    image_bytes = await photo_file.get_file().download_as_bytearray()
    
    database.add_certificate_receipt(user_id, image_bytes)
    
    await update.message.reply_text(config['messages']['certificate_pending'])

    caption = config['messages']['certificate_pending_admin_notification'].format(
        user_id=user_id,
        user_name=database.get_student_info(user_id)[2],
        course_name=user_data.get('course_name')
    )
    
    try:
        await context.bot.send_photo(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            photo=image_bytes,
            caption=caption
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification for certificate: {e}")
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"ERROR: Could not send certificate notification to channel. Details for student {user_id}:\n{caption}"
        )
    
    return ConversationHandler.END

async def re_enroll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'Re-enroll' button callback."""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text("Let's start the enrollment process again. What is your full name?")
    
    context.user_data['telegram_user_id'] = query.from_user.id
    context.user_data['chat_id'] = query.message.chat_id
    
    return ASKING_FULL_NAME


# --- Admin Command Handlers ---

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifies a student's payment and adds them to the course group."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    
    try:
        user_to_verify_id = int(re.search(r'_(\d+)', update.message.text).group(1))
    except (IndexError, ValueError, AttributeError):
        await update.message.reply_text("Usage: /verify_<telegram_user_id>")
        return
        
    student_info = database.get_student_info(user_to_verify_id)
    if not student_info:
        await update.message.reply_text(f"No student found with ID {user_to_verify_id}.")
        return

    _, student_id, student_name, _, course_name, _, status, student_chat_id, _, _, _, _ = student_info
    
    if status == 'verified':
        await update.message.reply_text(f"Student {student_id} is already verified.")
        return

    # Update status and save verification date
    verification_date = datetime.datetime.now()
    if database.update_payment_status(student_id, 'verified', verification_date):
        
        course_details = get_course_details(course_name)
        if not course_details:
            await update.message.reply_text(f"Error: Course details not found for {course_name}.")
            return
            
        try:
            await context.bot.unban_chat_member(chat_id=course_details['group_id'], user_id=student_id)
            verification_message = config['messages']['verification_success_student'].format(user_name=student_name)
            await context.bot.send_message(chat_id=student_chat_id, text=verification_message)
            admin_success_message = config['messages']['verification_success_admin'].format(user_name=student_name, user_id=student_id)
            await update.message.reply_text(admin_success_message)
        except Exception as e:
            logger.error(f"Error adding student {student_name} ({student_id}) to group: {e}")
            await update.message.reply_text(f"Error adding student {student_name} to group: {e}")
            if "user is a bot" in str(e):
                await context.bot.send_message(
                    chat_id=student_chat_id,
                    text=f"Congratulations, {student_name}! Your payment for the course has been verified. Please make sure you have joined the course group chat."
                )
            await update.message.reply_text(f"Student {student_name} ({student_id}) status updated, but group add failed.")
    else:
        await update.message.reply_text(f"Failed to update status for student {student_id}.")

async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Denies a student's enrollment."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
        
    try:
        user_to_deny_id = int(re.search(r'_(\d+)', update.message.text).group(1))
    except (IndexError, ValueError, AttributeError):
        await update.message.reply_text("Usage: /deny_<telegram_user_id>")
        return
        
    student_info = database.get_student_info(user_to_deny_id)
    if not student_info:
        await update.message.reply_text(f"No student found with ID {user_to_deny_id}.")
        return
        
    _, student_id, student_name, _, _, _, status, student_chat_id, _, _, _, _ = student_info

    if status == 'denied':
        await update.message.reply_text(f"Student {student_id} is already denied.")
        return

    if database.update_payment_status(student_id, 'denied'):
        await context.bot.send_message(
            chat_id=student_chat_id,
            text=config['messages']['verification_fail_student']
        )
        admin_denial_message = config['messages']['verification_fail_admin'].format(user_name=student_name, user_id=student_id)
        await update.message.reply_text(admin_denial_message)
    else:
        await update.message.reply_text(f"Failed to update status for student {student_id}.")

async def cert_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifies a student's certificate payment."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    
    try:
        user_to_verify_id = int(re.search(r'_(\d+)', update.message.text).group(1))
    except (IndexError, ValueError, AttributeError):
        await update.message.reply_text("Usage: /cert_verify_<telegram_user_id>")
        return
        
    student_info = database.get_student_info(user_to_verify_id)
    if not student_info:
        await update.message.reply_text(f"No student found with ID {user_to_verify_id}.")
        return

    _, student_id, student_name, _, _, _, _, student_chat_id, _, cert_status, _, _ = student_info
    
    if cert_status == 'verified':
        await update.message.reply_text(f"Certificate for student {student_id} is already verified.")
        return

    if database.update_certificate_status(student_id, 'verified'):
        await context.bot.send_message(
            chat_id=student_chat_id,
            text=config['messages']['certificate_verified_student'].format(user_name=student_name)
        )
        admin_success_message = config['messages']['certificate_verified_admin'].format(user_name=student_name, user_id=student_id)
        await update.message.reply_text(admin_success_message)
    else:
        await update.message.reply_text(f"Failed to update certificate status for student {student_id}.")


async def cert_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Denies a student's certificate payment."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
        
    try:
        user_to_deny_id = int(re.search(r'_(\d+)', update.message.text).group(1))
    except (IndexError, ValueError, AttributeError):
        await update.message.reply_text("Usage: /cert_deny_<telegram_user_id>")
        return
        
    student_info = database.get_student_info(user_to_deny_id)
    if not student_info:
        await update.message.reply_text(f"No student found with ID {user_to_deny_id}.")
        return
        
    _, student_id, student_name, _, _, _, _, student_chat_id, _, cert_status, _, _ = student_info

    if cert_status == 'denied':
        await update.message.reply_text(f"Certificate for student {student_id} is already denied.")
        return

    if database.update_certificate_status(student_id, 'denied'):
        await context.bot.send_message(
            chat_id=student_chat_id,
            text=config['messages']['certificate_denied_student']
        )
        admin_denial_message = config['messages']['certificate_denied_admin'].format(user_name=student_name, user_id=student_id)
        await update.message.reply_text(admin_denial_message)
    else:
        await update.message.reply_text(f"Failed to update certificate status for student {student_id}.")

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to list all pending enrollments and certificate applications."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
        
    pending_students = database.get_pending_students()
    cert_pending_students = database.get_pending_cert_students()
    
    message = "--- PENDING ENROLLMENTS ---\n"
    if pending_students:
        for student in pending_students:
            user_id, full_name, course = student
            message += f"• Name: {full_name}\n  ID: `{user_id}`\n  Course: {course}\n  Action: /verify_{user_id} or /deny_{user_id}\n\n"
    else:
        message += "No pending enrollments.\n\n"

    message += "--- PENDING CERTIFICATE PAYMENTS ---\n"
    if cert_pending_students:
        for student in cert_pending_students:
            user_id, full_name, course = student
            message += f"• Name: {full_name}\n  ID: `{user_id}`\n  Course: {course}\n  Action: /cert_verify_{user_id} or /cert_deny_{user_id}\n\n"
    else:
        message += "No pending certificate payments.\n"

    await update.message.reply_markdown_v2(message)

async def active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to list all active students with their expiry dates."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    active_students = database.get_active_students()
    
    if active_students:
        student_list_text = ""
        for student in active_students:
            user_id, full_name, course_name, verification_date_str = student
            course_details = get_course_details(course_name)
            if not course_details: continue
            
            verification_date = datetime.datetime.strptime(verification_date_str, '%Y-%m-%d %H:%M:%S.%f')
            expiry_date = verification_date + datetime.timedelta(days=course_details['duration_days'])
            
            student_list_text += f"• Name: {full_name}\n  ID: `{user_id}`\n  Course: {course_name}\n  Expires: {expiry_date.strftime('%Y-%m-%d')}\n\n"
        
        message = config['messages']['active_list'].format(student_list=student_list_text)
        await update.message.reply_markdown_v2(message)
    else:
        await update.message.reply_text("No active students found.")

async def expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to list all expired students."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    expired_students = database.get_expired_course_students()

    if expired_students:
        student_list_text = ""
        for student in expired_students:
            user_id, full_name, course_name, chat_id = student
            student_list_text += f"• Name: {full_name}\n  ID: `{user_id}`\n  Course: {course_name}\n\n"
        
        message = config['messages']['expired_list'].format(student_list=student_list_text)
        await update.message.reply_markdown_v2(message)
    else:
        await update.message.reply_text("No expired students found.")


async def start_certificate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A command handler that serves as a direct entry point for the certificate conversation."""
    return await certificate_entry(update, context)


def main() -> None:
    """Start the bot."""
    database.create_table_if_not_exists()
    
    application = Application.builder().token(BOT_API_TOKEN).build()

    # Schedule a daily job for automated checks
    job_queue = application.job_queue
    job_queue.run_daily(check_expiry_and_notify, time=datetime.time(hour=10, minute=0, tzinfo=datetime.timezone.utc))

    # Enrollment Conversation
    enrollment_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASKING_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_full_name)],
            ASKING_PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_number)],
            ASKING_COURSE_SELECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_course_selection)],
            WAITING_FOR_RECEIPT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, receive_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Certificate Conversation
    cert_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("certificate", start_certificate_command)],
        states={
            WAITING_FOR_CERTIFICATE_RECEIPT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, receive_certificate_receipt)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END # Allows canceling a sub-conversation
        }
    )

    application.add_handler(enrollment_conv_handler)
    application.add_handler(cert_conv_handler)
    
    # Admin command handlers
    application.add_handler(CommandHandler(re.compile(r"verify_\d+"), verify))
    application.add_handler(CommandHandler(re.compile(r"deny_\d+"), deny))
    application.add_handler(CommandHandler(re.compile(r"cert_verify_\d+"), cert_verify))
    application.add_handler(CommandHandler(re.compile(r"cert_deny_\d+"), cert_deny))
    application.add_handler(CommandHandler("pending", pending))
    application.add_handler(CommandHandler("active", active))
    application.add_handler(CommandHandler("expired", expired))
    
    # Callback handler for the re-enroll button
    application.add_handler(MessageHandler(filters.Regex('^re_enroll$'), re_enroll_callback))


    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
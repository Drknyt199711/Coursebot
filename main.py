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
    ConversationHandler,
    CallbackQueryHandler
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
    WAITING_FOR_RECEIPT,
    ASKING_CERTIFICATE_CONFIRMATION,
    WAITING_FOR_CERTIFICATE_RECEIPT,
    EDITING_CONFIG_SECTION,
    EDITING_CONFIG_KEY,
    EDITING_CONFIG_VALUE,
    EDITING_COURSE_INDEX,
    EDITING_COURSE_FIELD
) = range(11)

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

async def reload_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reloads the configuration from config.json."""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    global config
    global PAYMENT_CONFIRMATION_CHANNEL_ID
    global COURSE_OPTIONS
    global BANK_DETAILS

    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)

        PAYMENT_CONFIRMATION_CHANNEL_ID = config['bot']['payment_confirmation_channel_id']
        EXPIRY_NOTIFICATION_GROUP_ID = config['bot']['expiry_notification_group_id']
        COURSE_OPTIONS = [course['name'] for course in config['courses']]
        BANK_DETAILS = (
            f"Bank Name: {config['bank_details']['name']}\n"
            f"Account Holder: {config['bank_details']['account_holder']}\n"
            f"Account Number: {config['bank_details']['account_number']}"
        )
        
        logger.info("Configuration has been successfully reloaded.")
        await update.message.reply_text("✅ Configuration reloaded successfully!")
    except FileNotFoundError:
        error_msg = "❌ Error: config.json not found."
        logger.error(error_msg)
        await update.message.reply_text(error_msg)
    except json.JSONDecodeError:
        error_msg = "❌ Error: Failed to parse config.json. Please check for syntax errors."
        logger.error(error_msg)
        await update.message.reply_text(error_msg)

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all available commands for the admin."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    commands = [
        "📌 **Admin Commands:**",
        "/pending - List pending enrollments and certificate applications",
        "/active - List active students with expiry dates",
        "/expired - List expired students",
        "/reload_config - Reload configuration from file",
        "/edit_config - Edit bot configuration",
        "",
        "📌 **Verification Commands:**",
        "/verify_<user_id> - Approve a student's enrollment",
        "/deny_<user_id> - Deny a student's enrollment",
        "/cert_verify_<user_id> - Approve a certificate payment",
        "/cert_deny_<user_id> - Deny a certificate payment",
        "",
        "📌 **Student Commands:**",
        "/start - Begin enrollment process",
        "/certificate - Apply for a course certificate"
    ]

    await update.message.reply_text("\n".join(commands), parse_mode='Markdown')

async def edit_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the config editing process."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return ConversationHandler.END

    keyboard = [
        ["Bot Settings", "Messages"],
        ["Courses", "Bank Details"],
        ["Cancel"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "Which section of the config would you like to edit?",
        reply_markup=reply_markup
    )
    return EDITING_CONFIG_SECTION

async def edit_config_section(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles selection of config section to edit."""
    section = update.message.text.lower().replace(" ", "_")
    context.user_data['config_section'] = section
    
    if section == "cancel":
        await update.message.reply_text("Config editing cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    if section == "courses":
        keyboard = [[course['name'] for course in config['courses']] + ["Add New Course"], ["Cancel"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "Select a course to edit or 'Add New Course':",
            reply_markup=reply_markup
        )
        return EDITING_COURSE_INDEX
    else:
        if section not in config:
            await update.message.reply_text("Invalid section. Please try again.")
            return EDITING_CONFIG_SECTION
            
        keys = list(config[section].keys())
        keyboard = [[key] for key in keys] + [["Cancel"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Which key in '{section}' would you like to edit?",
            reply_markup=reply_markup
        )
        return EDITING_CONFIG_KEY

async def edit_config_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles selection of config key to edit."""
    key = update.message.text
    context.user_data['config_key'] = key
    
    if key == "Cancel":
        await update.message.reply_text("Config editing cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    section = context.user_data['config_section']
    current_value = config[section][key]
    
    if isinstance(current_value, dict):
        keys = list(current_value.keys())
        keyboard = [[key] for key in keys] + [["Cancel"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Which nested key in '{section}.{key}' would you like to edit?",
            reply_markup=reply_markup
        )
        context.user_data['nested_section'] = section
        context.user_data['nested_key'] = key
        return EDITING_CONFIG_KEY
    else:
        await update.message.reply_text(
            f"Current value for '{section}.{key}':\n{current_value}\n\n"
            f"Please enter the new value:",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDITING_CONFIG_VALUE

async def edit_config_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles entering a new config value."""
    new_value = update.message.text
    section = context.user_data.get('config_section')
    key = context.user_data.get('config_key')
    
    if 'nested_section' in context.user_data:
        nested_section = context.user_data['nested_section']
        nested_key = context.user_data['nested_key']
        
        try:
            new_value = int(new_value)
        except ValueError:
            try:
                new_value = float(new_value)
            except ValueError:
                pass
        
        config[nested_section][nested_key][key] = new_value
        await update.message.reply_text(
            f"Updated {nested_section}.{nested_key}.{key} to:\n{new_value}"
        )
    else:
        if new_value.lower() in ['true', 'false']:
            new_value = new_value.lower() == 'true'
        else:
            try:
                new_value = int(new_value)
            except ValueError:
                try:
                    new_value = float(new_value)
                except ValueError:
                    pass
        
        config[section][key] = new_value
        await update.message.reply_text(
            f"Updated {section}.{key} to:\n{new_value}"
        )
    
    try:
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        await update.message.reply_text("✅ Config saved successfully!")
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        await update.message.reply_text(f"❌ Error saving config: {e}")
    
    return ConversationHandler.END

async def edit_course_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles selection of course to edit."""
    choice = update.message.text
    context.user_data['course_choice'] = choice
    
    if choice == "Cancel":
        await update.message.reply_text("Config editing cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    if choice == "Add New Course":
        new_course = {
            "name": "New Course",
            "price": 0,
            "group_id": 0,
            "duration_days": 30,
            "certificate_price": 0,
            "certificate_wait_days": 0
        }
        config['courses'].append(new_course)
        context.user_data['course_index'] = len(config['courses']) - 1
        
        try:
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            await update.message.reply_text(f"❌ Error saving config: {e}")
            return ConversationHandler.END
        
        await update.message.reply_text("Created new course. Now editing...")
    else:
        for i, course in enumerate(config['courses']):
            if course['name'] == choice:
                context.user_data['course_index'] = i
                break
        else:
            await update.message.reply_text("Course not found. Please try again.")
            return EDITING_COURSE_INDEX
    
    course_fields = list(config['courses'][0].keys())
    keyboard = [[field] for field in course_fields] + [["Cancel"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "Which field would you like to edit?",
        reply_markup=reply_markup
    )
    return EDITING_COURSE_FIELD

async def edit_course_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles editing a specific course field."""
    field = update.message.text
    context.user_data['course_field'] = field
    
    if field == "Cancel":
        await update.message.reply_text("Config editing cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    course_index = context.user_data['course_index']
    current_value = config['courses'][course_index][field]
    
    await update.message.reply_text(
        f"Current value for '{field}':\n{current_value}\n\n"
        f"Please enter the new value:",
        reply_markup=ReplyKeyboardRemove()
    )
    return EDITING_CONFIG_VALUE

async def edit_course_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles entering a new course field value."""
    new_value = update.message.text
    course_index = context.user_data['course_index']
    field = context.user_data['course_field']
    
    if field in ['price', 'group_id', 'duration_days', 'certificate_price', 'certificate_wait_days']:
        try:
            new_value = int(new_value)
        except ValueError:
            await update.message.reply_text("Please enter a valid integer for this field.")
            return EDITING_CONFIG_VALUE
    
    config['courses'][course_index][field] = new_value
    
    try:
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        await update.message.reply_text(
            f"✅ Updated course '{config['courses'][course_index]['name']}.{field}' to:\n{new_value}\n\n"
            "Config saved successfully!"
        )
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        await update.message.reply_text(f"❌ Error saving config: {e}")
    
    return ConversationHandler.END

# --- Command Handlers for Enrollment Conversation ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation with an enrollment button."""
    user = update.effective_user
    welcome_message = config['messages']['welcome'].format(user_mention=user.mention_html())
    
    keyboard = [
        [InlineKeyboardButton("Enroll Now", callback_data="start_enrollment")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        welcome_message,
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def start_enrollment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the 'Enroll Now' button press."""
    query = update.callback_query
    await query.answer()
    
    context.user_data['telegram_user_id'] = query.from_user.id
    context.user_data['chat_id'] = query.message.chat_id
    
    await query.message.reply_text("Great! What is your full name?")
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
    image_bytes = await (await photo_file.get_file()).download_as_bytearray()
    
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
    
    photo_file = update.message.photo[-1]
    
    try:
        await context.bot.send_photo(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            photo=photo_file.file_id,
            caption=caption
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification: {e}")
        full_caption = (
            f"⚠️ **Error: Photo Too Large!** ⚠️\n\n"
            f"{caption}\n"
            f"File ID: `{photo_file.file_id}`\n"
            f"You can view the full file by forwarding it from the student's chat."
        )
        await context.bot.send_message(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            text=full_caption,
            parse_mode='Markdown'
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
    image_bytes = await (await photo_file.get_file()).download_as_bytearray()
    
    database.add_certificate_receipt(user_id, image_bytes)
    
    await update.message.reply_text(config['messages']['certificate_pending'])

    caption = config['messages']['certificate_pending_admin_notification'].format(
        user_id=user_id,
        user_name=database.get_student_info(user_id)[2],
        course_name=user_data.get('course_name')
    )
    
    photo_file = update.message.photo[-1]

    try:
        await context.bot.send_photo(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            photo=photo_file.file_id,
            caption=caption
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification for certificate: {e}")
        full_caption = (
            f"⚠️ **Error: Certificate Photo Too Large!** ⚠️\n\n"
            f"{caption}\n"
            f"File ID: `{photo_file.file_id}`\n"
            f"You can view the full file by forwarding it from the student's chat."
        )
        await context.bot.send_message(
            chat_id=PAYMENT_CONFIRMATION_CHANNEL_ID,
            text=full_caption,
            parse_mode='Markdown'
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
    
    # Add command handlers
    application.add_handler(CommandHandler("cmd", cmd))
    application.add_handler(CommandHandler("reload_config", reload_config))
    
    # Config editing conversation handler
    config_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("edit_config", edit_config)],
        states={
            EDITING_CONFIG_SECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_config_section)],
            EDITING_CONFIG_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_config_key)],
            EDITING_CONFIG_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_config_value)],
            EDITING_COURSE_INDEX: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_course_index)],
            EDITING_COURSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_course_field)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Enrollment Conversation
    enrollment_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start_enrollment_callback, pattern="^start_enrollment$")
        ],
        states={
            ASKING_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_full_name)],
            ASKING_PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_number)],
            ASKING_COURSE_SELECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_course_selection)],
            WAITING_FOR_RECEIPT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, receive_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Certificate Conversation
    cert_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("certificate", start_certificate_command)],
        states={
            WAITING_FOR_CERTIFICATE_RECEIPT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, receive_certificate_receipt)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )

    application.add_handler(config_conv_handler)
    application.add_handler(enrollment_conv_handler)
    application.add_handler(cert_conv_handler)
    
    # Admin command handlers
    application.add_handler(MessageHandler(filters.Regex(r"^/verify_\d+$") & filters.COMMAND, verify))
    application.add_handler(MessageHandler(filters.Regex(r"^/deny_\d+$") & filters.COMMAND, deny))
    application.add_handler(MessageHandler(filters.Regex(r"^/cert_verify_\d+$") & filters.COMMAND, cert_verify))
    application.add_handler(MessageHandler(filters.Regex(r"^/cert_deny_\d+$") & filters.COMMAND, cert_deny))
    application.add_handler(CommandHandler("pending", pending))
    application.add_handler(CommandHandler("active", active))
    application.add_handler(CommandHandler("expired", expired))
    
    # Callback handler for the re-enroll button
    application.add_handler(CallbackQueryHandler(re_enroll_callback, pattern="^re_enroll$"))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
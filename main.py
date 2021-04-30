import json
import logging
import re
import time
from copy import deepcopy
from enum import Enum
from typing import List
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot, MAX_MESSAGE_LENGTH, BotCommand
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
from jinja2 import Template
from peewee import SqliteDatabase, Model, DateTimeField, CharField, FixedCharField, IntegerField, BooleanField

from cowinapi import CoWinAPI, VaccinationCenter
from secrets import TELEGRAM_BOT_TOKEN

PINCODE_PREFIX_REGEX = r'^\s*(pincode)?\s*(?P<pincode_mg>\d{6})\s*'
AGE_BUTTON_REGEX = r'^age: (?P<age_mg>\d+)'
CMD_BUTTON_REGEX = r'^cmd: (?P<cmd_mg>.+)'
DISABLE_TEXT_REGEX = r'\s*disable|stop|pause\s*'

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

CoWinAPIObj = CoWinAPI()

db = SqliteDatabase('users.db')


class AgeRangePref(Enum):
    Unknown = 0
    MinAge18 = 1
    MinAge45 = 2
    MinAgeAny = 3

    def __str__(self) -> str:
        if self == AgeRangePref.MinAge18:
            return "18+"
        elif self == AgeRangePref.MinAge45:
            return "45+"
        else:
            return "Both"


class EnumField(IntegerField):

    def __init__(self, choices, *args, **kwargs):
        super(IntegerField, self).__init__(*args, **kwargs)
        self.choices = choices

    def db_value(self, value) -> int:
        return value.value

    def python_value(self, value):
        return self.choices(value)


# storage classes
class User(Model):
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)
    last_alert_sent_at: datetime = DateTimeField(default=datetime.now)
    total_alerts_sent = IntegerField(default=0)
    telegram_id = CharField(max_length=220, unique=True)
    chat_id = CharField(max_length=220)
    pincode: str = FixedCharField(max_length=6, null=True, index=True)
    age_limit: AgeRangePref = EnumField(choices=AgeRangePref, default=AgeRangePref.Unknown)
    enabled = BooleanField(default=False, index=True)

    class Meta:
        database = db


def sanitise_msg(msg: str) -> str:
    if len(msg) < MAX_MESSAGE_LENGTH:
        return msg

    help_text = "\n\n (message truncated due to size)"
    msg_length = MAX_MESSAGE_LENGTH - len(help_text)
    return msg[:msg_length] + help_text


def get_main_buttons() -> List[InlineKeyboardButton]:
    return [
        InlineKeyboardButton("🔔 Setup Alert", callback_data='cmd: setup_alert'),
        InlineKeyboardButton("💼 Check Open Slots", callback_data='cmd: check_slots'),
    ]


def get_age_kb() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("18+", callback_data='age: 1'),
            InlineKeyboardButton("45+", callback_data='age: 2'),
            InlineKeyboardButton("Both", callback_data='age: 3'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            *get_main_buttons()
        ],
        [
            InlineKeyboardButton("💡 Help", callback_data='cmd: help'),
            InlineKeyboardButton("🤖 About", callback_data='cmd: about')
        ],
    ])


def start(update: Update, _: CallbackContext) -> None:
    update.message.reply_text(
        f"Hey {update.effective_user.username}! Welcome to Cowin Assist Bot\n\nThis bot will help you"
        " check available slots and also, set an alert when a slot becomes available",
        reply_markup=get_main_keyboard(),
    )


def cmd_button_handler(update: Update, ctx: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    if cmd := ctx.match.groupdict().get("cmd_mg"):
        cmd = cmd.strip()
        if cmd == "setup_alert":
            setup_alert_command(update, ctx)
            return
        elif cmd == "check_slots":
            check_slots_command(update, ctx)
            return
        else:
            update.effective_chat.send_message("cmd not implemented yet")
            return


def check_if_preferences_are_set(update: Update, _: CallbackContext) -> User:
    user: User
    user, _ = get_or_create_user(telegram_id=update.effective_user.id, chat_id=update.effective_chat.id)
    if user.age_limit is None or user.age_limit == AgeRangePref.Unknown:
        update.effective_chat.send_message("select age pref", reply_markup=get_age_kb())
        return
    if user.pincode is None:
        update.effective_chat.send_message("pincode not setup")
        return
    return user


def setup_alert_command(update: Update, ctx: CallbackContext) -> None:
    user = check_if_preferences_are_set(update, ctx)
    if not user:
        return
    user.enabled = True
    user.save()
    update.effective_chat.send_message("Alerts are enabled. Click on /pause to pause the alerts")


def disable_alert_command(update: Update, _: CallbackContext) -> None:
    user: User
    user, _ = get_or_create_user(telegram_id=update.effective_user.id, chat_id=update.effective_chat.id)
    user.enabled = False
    user.save()
    update.effective_chat.send_message("Alerts are disabled. Click on /resume to resume the alerts")


def get_available_centers_by_pin(pincode: str) -> List[VaccinationCenter]:
    vaccination_centers = CoWinAPIObj.calender_by_pin(pincode, CoWinAPI.today())
    if vaccination_centers:
        vaccination_centers = [vc for vc in vaccination_centers if vc.has_available_sessions()]
    return vaccination_centers


def get_formatted_message(centers: List[VaccinationCenter]) -> str:
    # TODO: Fix this shit
    template = """
{%- for c in centers %}
*{{ c.name }}* {%- if c.fee_type == 'Paid' %}*(Paid)*{%- endif %}:{% for s in c.get_available_sessions() %}
    • {{s.date}}: {{s.capacity}}{% endfor %}
{% endfor %}"""
    tm = Template(template)
    return tm.render(centers=centers)


# filtering based on the age preferences set by the user
def filter_centers_by_age_limit(age_limit: AgeRangePref, centers: List[VaccinationCenter]) -> List[VaccinationCenter]:
    # if user hasn't set any age preferences, then just show everything
    if age_limit in [None, AgeRangePref.MinAgeAny, AgeRangePref.Unknown]:
        return centers

    filter_age: int
    if age_limit == AgeRangePref.MinAge18:
        filter_age = 18
    else:
        filter_age = 45

    # TODO: FIX THIS! This makes a deep copy of Vaccination Center objects
    centers_copy: List[VaccinationCenter] = deepcopy(centers)
    for vc in centers_copy:
        vc.sessions = vc.get_available_sessions_by_age_limit(filter_age)

    results: List[VaccinationCenter] = [vc for vc in centers_copy if vc.has_available_sessions()]
    return results


def get_message_header(user: User) -> str:
    return F"Following slots are available (pincode: {user.pincode}, age limit: {user.age_limit})\n"


def check_slots_command(update: Update, ctx: CallbackContext) -> None:
    user = check_if_preferences_are_set(update, ctx)
    if not user:
        return

    vaccination_centers = get_available_centers_by_pin(user.pincode)
    vaccination_centers = filter_centers_by_age_limit(user.age_limit, vaccination_centers)
    if not vaccination_centers:
        update.effective_chat.send_message(
            F"Sorry, not free slots available (pincode: {user.pincode}, age limit: {user.age_limit})")
        return

    msg: str = get_formatted_message(vaccination_centers)
    msg = get_message_header(user=user) + msg
    update.effective_chat.send_message(sanitise_msg(msg), parse_mode='markdown')
    return


def help_command(update: Update, _: CallbackContext) -> None:
    update.message.reply_text('Help!', reply_markup=get_age_kb())


def echo(update: Update, _: CallbackContext) -> None:
    update.message.reply_text("okay bye")


def get_or_create_user(telegram_id: str, chat_id) -> (User, bool):
    return User.get_or_create(telegram_id=telegram_id,
                              defaults={'chat_id': chat_id})


def set_age_preference(update: Update, ctx: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    age_pref = ctx.match.groupdict().get("age_mg")
    if age_pref is None:
        return

    user: User
    user, _ = get_or_create_user(telegram_id=update.effective_user.id, chat_id=update.effective_chat.id)
    user.age_limit = AgeRangePref(int(age_pref))
    user.updated_at = datetime.now()
    user.save()

    if user.pincode:
        update.effective_chat.send_message("Age preference has been set",
                                           reply_markup=InlineKeyboardMarkup([[*get_main_buttons()]]))
    else:
        update.effective_chat.send_message("Age preference has been set. Please enter your pincode to proceed next")


def set_pincode(update: Update, ctx: CallbackContext) -> None:
    pincode = ctx.match.groupdict().get("pincode_mg")
    if not pincode:
        return
    user: User
    user, _ = get_or_create_user(telegram_id=update.effective_user.id, chat_id=update.effective_chat.id)
    user.pincode = pincode
    user.updated_at = datetime.now()
    user.save()

    if user.age_limit:
        update.effective_chat.send_message(F"Pincode is set to {pincode}",
                                           reply_markup=InlineKeyboardMarkup([[*get_main_buttons()]]))
    else:
        update.effective_chat.send_message("Pincode is set. Please set the age preference to proceed next",
                                           reply_markup=get_age_kb())


def background_worker():
    # query db
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    time_now = datetime.now()
    # find all distinct pincodes where pincode is not null and at least one user exists with alerts enabled
    for distinct_user in User.select(User.pincode).where(
            User.pincode.is_null(False), User.enabled == True).distinct():
        # get all the available vaccination centers with open slots
        vaccination_centers = get_available_centers_by_pin(distinct_user.pincode)
        if not vaccination_centers:
            continue
        # find all users for this pincode and alerts enabled
        for user in User.select().where(User.pincode == distinct_user.pincode, User.enabled == True):
            # if user age limit is not 18, then we shouldn't ping them too often
            if not user.age_limit == AgeRangePref.MinAge18:
                delta = time_now - user.last_alert_sent_at
                if delta.seconds < 60 * 60:
                    continue
            filtered_centers = filter_centers_by_age_limit(user.age_limit, vaccination_centers)
            if not vaccination_centers:
                continue
            msg: str = get_formatted_message(filtered_centers)
            msg = "*[ALERT!]* " + get_message_header(user=user) + msg + \
                  "\n Click on /pause to disable the notifications"
            bot.send_message(chat_id=user.chat_id, text=sanitise_msg(msg), parse_mode='markdown')
            user.last_alert_sent_at = time_now
            user.total_alerts_sent += 1
            user.save()


def main() -> None:
    # initialise bot and set commands
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    bot.set_my_commands([
        BotCommand(command='start', description='start the bot session'),
        BotCommand(command='help', description='provide help on how to use the bot'),
        BotCommand(command='alert', description='enable alerts on new slots'),
        BotCommand(command='resume', description='enable alerts on new slots'),
        BotCommand(command='pause', description='disable alerts on new slots'),
    ])

    # connect and create tables
    db.connect()
    db.create_tables([User, ])

    updater = Updater(TELEGRAM_BOT_TOKEN)

    # Add handlers
    updater.dispatcher.add_handler(CommandHandler("start", start))
    updater.dispatcher.add_handler(CommandHandler("help", help_command))
    updater.dispatcher.add_handler(CommandHandler("alert", setup_alert_command))
    updater.dispatcher.add_handler(CommandHandler("resume", setup_alert_command))
    updater.dispatcher.add_handler(CommandHandler("pause", disable_alert_command))
    updater.dispatcher.add_handler(CallbackQueryHandler(set_age_preference, pattern=AGE_BUTTON_REGEX))
    updater.dispatcher.add_handler(CallbackQueryHandler(cmd_button_handler, pattern=CMD_BUTTON_REGEX))
    updater.dispatcher.add_handler(MessageHandler(Filters.regex(
        re.compile(PINCODE_PREFIX_REGEX, re.IGNORECASE)), set_pincode))
    updater.dispatcher.add_handler(MessageHandler(Filters.regex(
        re.compile(DISABLE_TEXT_REGEX, re.IGNORECASE)), disable_alert_command))
    updater.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    # Start the Bot
    updater.start_polling()

    try:
        while True:
            background_worker()
            time.sleep(30)  # sleep for 30 seconds
    except Exception as e:
        logger.exception("bg worker failed", exc_info=e)
    updater.idle()


if __name__ == '__main__':
    main()
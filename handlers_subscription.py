"""
Подписка через встроенные платежи Telegram с провайдером ЮKassa.

Как это работает технически:
1. В @BotFather подключается провайдер ЮKassa (Payments -> ЮKassa), это даёт
   provider_token — его кладём в переменную YOOKASSA_PROVIDER_TOKEN.
2. Бот вызывает bot.send_invoice(...) — Telegram сам показывает пользователю
   красивый счёт и форму оплаты российской картой. Наш сервер вообще не видит
   номер карты — это большой плюс с точки зрения безопасности и упрощения.
3. Telegram присылает pre_checkout_query — на него нужно ответить ok=True
   в течение 10 секунд, иначе платёж отменится.
4. После реальной оплаты Telegram присылает сообщение с successful_payment —
   вот тут мы и продлеваем подписку в своей БД (SQLite, никаких доп. сервисов).

Никакой отдельной "базы подписок" не нужно: срок подписки — это просто одно
поле subscription_until в таблице users. Это и есть "своя БД" в оптимальном
виде — без лишних таблиц и джойнов, дешёво для Railway.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import database as db
from chat_cleanup import clean, track
from config import YOOKASSA_PROVIDER_TOKEN
from keyboards import subscription_plans_kb

router = Router()

# Тарифы: ключ -> (название, дней, цена в рублях)
PLANS = {
    "1m": {"title": "1 месяц", "days": 30, "price_rub": 149},
    "3m": {"title": "3 месяца", "days": 90, "price_rub": 349},
    "12m": {"title": "12 месяцев", "days": 365, "price_rub": 990},
}


@router.message(F.text == "💎 Подписка")
@router.message(Command("subscribe"))
async def subscribe_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)

    if db.is_pro(user):
        until = user.subscription_until.strftime("%d.%m.%Y")
        sent = await message.answer(f"У тебя уже активна подписка до {until}. Можно продлить заранее:")
    else:
        sent = await message.answer(
            "💎 Подписка снимает лимит на количество книг и включает ИИ-определение "
            "циклов/серий (с частью и общим количеством книг). Выбери тариф:"
        )
    track(message.chat.id, sent.message_id)

    if not YOOKASSA_PROVIDER_TOKEN:
        sent = await message.answer(
            "⚠️ Оплата пока не настроена: не задан YOOKASSA_PROVIDER_TOKEN "
            "в переменных окружения."
        )
        track(message.chat.id, sent.message_id)
        return

    sent = await message.answer("Тарифы:", reply_markup=subscription_plans_kb(PLANS))
    track(message.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery) -> None:
    plan_key = callback.data.split(":")[1]
    plan = PLANS.get(plan_key)
    await callback.answer()
    if not plan:
        return

    await callback.message.answer_invoice(
        title=f"Подписка на {plan['title']}",
        description="Безлимитная библиотека + определение циклов/серий книг через ИИ.",
        payload=f"sub:{plan_key}",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=plan["title"], amount=plan["price_rub"] * 100)],
    )


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    # Обязательно ответить в течение 10 секунд, иначе Telegram отменит оплату
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    payload = message.successful_payment.invoice_payload  # "sub:1m"
    plan_key = payload.split(":")[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await message.answer("Оплата прошла, но тариф не распознан — напиши в поддержку.")
        return

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    user = await db.extend_subscription(user.id, plan["days"])
    until = user.subscription_until.strftime("%d.%m.%Y")
    await message.answer(f"✅ Оплата получена! Подписка активна до {until}. Спасибо!")

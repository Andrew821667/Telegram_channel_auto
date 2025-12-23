"""
Telegram Bot Keyboards
Клавиатуры для модерации и управления ботом.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_draft_review_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для модерации драфта.

    Args:
        draft_id: ID драфта

    Returns:
        InlineKeyboardMarkup с кнопками одобрения/отклонения
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✅ Опубликовать",
            callback_data=f"publish:{draft_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Редактировать",
            callback_data=f"edit:{draft_id}"
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"reject:{draft_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data=f"stats:{draft_id}"
        )
    )

    return builder.as_markup()


def get_confirm_keyboard(action: str, draft_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения действия.

    Args:
        action: Действие (publish, reject)
        draft_id: ID драфта

    Returns:
        InlineKeyboardMarkup с кнопками подтверждения
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✅ Да, подтвердить",
            callback_data=f"confirm_{action}:{draft_id}"
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"cancel:{draft_id}"
        )
    )

    return builder.as_markup()


def get_reader_keyboard(source_url: str) -> InlineKeyboardMarkup:
    """
    Клавиатура для читателей в опубликованном посте.

    Args:
        source_url: URL источника новости

    Returns:
        InlineKeyboardMarkup с кнопками для читателей
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📖 Читать полностью",
            url=source_url
        )
    )

    return builder.as_markup()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Главное меню админ панели.

    Returns:
        InlineKeyboardMarkup с главными командами
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📝 Новые драфты",
            callback_data="show_drafts"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Запустить сбор",
            callback_data="run_fetch"
        ),
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="show_stats"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⚙️ Настройки",
            callback_data="show_settings"
        )
    )

    return builder.as_markup()


def get_rejection_reasons_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура с причинами отклонения.

    Args:
        draft_id: ID драфта

    Returns:
        InlineKeyboardMarkup с типовыми причинами отклонения
    """
    builder = InlineKeyboardBuilder()

    reasons = [
        ("Нерелевантно", "irrelevant"),
        ("Низкое качество", "low_quality"),
        ("Дубликат", "duplicate"),
        ("Неточная информация", "inaccurate"),
        ("Другое", "other"),
    ]

    for text, reason in reasons:
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"reject_reason:{draft_id}:{reason}"
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="« Назад",
            callback_data=f"back_to_draft:{draft_id}"
        )
    )

    return builder.as_markup()

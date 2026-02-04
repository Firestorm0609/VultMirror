"""
Input Validation Module
=======================
Validates user inputs for security and correctness
"""

import re
from typing import Optional, Tuple

def validate_api_id(api_id: str) -> Tuple[bool, str]:
    """Validate Telegram API ID"""
    if not api_id:
        return False, "API ID is required"
    if not api_id.isdigit():
        return False, "API ID must be numeric"
    if len(api_id) < 5 or len(api_id) > 12:
        return False, "API ID must be 5-12 digits"
    return True, "Valid"

def validate_api_hash(api_hash: str) -> Tuple[bool, str]:
    """Validate Telegram API Hash"""
    if not api_hash:
        return False, "API Hash is required"
    if len(api_hash) != 32:
        return False, "API Hash must be 32 characters"
    if not re.match(r'^[a-f0-9]+$', api_hash.lower()):
        return False, "API Hash must be hexadecimal"
    return True, "Valid"

def validate_phone(phone: str) -> Tuple[bool, str]:
    """Validate phone number"""
    if not phone:
        return False, "Phone number is required"
    if not phone.startswith('+'):
        return False, "Phone must start with + and country code"
    # Remove + and check if rest is digits
    digits = phone[1:].replace(' ', '').replace('-', '')
    if not digits.isdigit():
        return False, "Phone must contain only digits after +"
    if len(digits) < 10 or len(digits) > 15:
        return False, "Phone must be 10-15 digits"
    return True, "Valid"

def validate_chat_id(chat_id: str) -> Tuple[bool, Optional[int], str]:
    """Validate Telegram chat ID"""
    try:
        chat_id_int = int(chat_id.strip())
        return True, chat_id_int, "Valid"
    except ValueError:
        return False, None, "Chat ID must be a number (e.g., -1001234567890)"

def sanitize_message(text: str, max_length: int = 500) -> str:
    """Sanitize and truncate message text"""
    if not text:
        return ""
    # Remove potential markdown injection
    text = text.replace('`', "'")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text

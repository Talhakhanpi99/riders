from voice_core import IntentParser, IntentType


def test_parse_roman_urdu_call() -> None:
    parser = IntentParser()
    intent = parser.parse("Shani ko call lagao")

    assert intent.intent_type == IntentType.CALL_CONTACT
    assert "shani" in intent.entities["contact_name"]


def test_parse_sms_message() -> None:
    parser = IntentParser()
    intent = parser.parse("Ali ko bolo main aa raha hun")

    assert intent.intent_type == IntentType.SEND_SMS
    assert "ali" in intent.entities["contact_name"]
    assert "main aa raha hun" in intent.entities["message"]


def test_parse_battery_status() -> None:
    parser = IntentParser()
    intent = parser.parse("battery percentage")

    assert intent.intent_type == IntentType.BATTERY_STATUS


def test_parse_torch_and_brightness_commands() -> None:
    parser = IntentParser()

    assert parser.parse("open the torch").intent_type == IntentType.FLASHLIGHT_ON
    assert parser.parse("close the light").intent_type == IntentType.FLASHLIGHT_OFF

    brightness = parser.parse("brightness 55 percent")
    assert brightness.intent_type == IntentType.BRIGHTNESS_SET
    assert brightness.entities["percentage"] == "55"


def test_parse_english_sms_contact_and_message() -> None:
    parser = IntentParser()
    intent = parser.parse("send message to Shani Zong that I am on my way")

    assert intent.intent_type == IntentType.SEND_SMS
    assert intent.entities["contact_name"] == "shani zong"
    assert intent.entities["message"] == "i am on my way"


def test_short_confirmation_does_not_match_contact_name() -> None:
    parser = IntentParser()
    intent = parser.parse("Shani Zong")

    assert intent.intent_type != IntentType.CONFIRM_ACTION
    assert intent.intent_type != IntentType.BRIGHTNESS_SET

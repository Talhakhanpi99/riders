from app import Database, SettingsRepository


def test_settings_round_trip(tmp_path) -> None:
    database = Database(tmp_path / "voice.db")
    repository = SettingsRepository(database)

    repository.set_value("wake_word", "rider")

    assert repository.get_all().wake_word == "rider"

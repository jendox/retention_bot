from enum import StrEnum


class Timezone(StrEnum):
    # Европа / СНГ
    EUROPE_MINSK = "Europe/Minsk"
    EUROPE_MOSCOW = "Europe/Moscow"
    EUROPE_KIEV = "Europe/Kiev"
    EUROPE_WARSAW = "Europe/Warsaw"
    EUROPE_VILNIUS = "Europe/Vilnius"
    EUROPE_RIGA = "Europe/Riga"
    EUROPE_TALLINN = "Europe/Tallinn"

    # Восточная Европа / Кавказ
    EUROPE_BUCHAREST = "Europe/Bucharest"
    EUROPE_CHISINAU = "Europe/Chisinau"
    ASIA_YEREVAN = "Asia/Yerevan"
    ASIA_TBILISI = "Asia/Tbilisi"

    # Средняя Азия
    ASIA_ALMATY = "Asia/Almaty"
    ASIA_BISHKEK = "Asia/Bishkek"
    ASIA_TASHKENT = "Asia/Tashkent"

    # Европа (основные)
    EUROPE_LONDON = "Europe/London"
    EUROPE_PARIS = "Europe/Paris"
    EUROPE_BERLIN = "Europe/Berlin"
    EUROPE_ROME = "Europe/Rome"
    EUROPE_MADRID = "Europe/Madrid"
    EUROPE_AMSTERDAM = "Europe/Amsterdam"

    # США (часто встречаются при регистрации Telegram)
    AMERICA_NEW_YORK = "America/New_York"
    AMERICA_CHICAGO = "America/Chicago"
    AMERICA_DENVER = "America/Denver"
    AMERICA_LOS_ANGELES = "America/Los_Angeles"


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class InviteType(StrEnum):
    MASTER = "master"
    CLIENT = "client"

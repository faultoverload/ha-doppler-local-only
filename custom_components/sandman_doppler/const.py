"""Constants for Sandman Doppler Clocks."""

# Base component constants
NAME = "Sandman Doppler"
DOMAIN = "sandman_doppler"

# Config flow constants
CONF_HOST = "host"
CONF_PORT = "port"
CONF_LOCAL_KEY = "local_key"
CONF_DSN = "dsn"

ATTR_DSN = "dsn"
ATTR_BUTTON = "button"
CONF_SUBTYPE = "subtype"

ATTR_DOPPLER_NAME = "doppler_name"

EVENT_BUTTON_PRESSED = f"{DOMAIN}_button_pressed"

SERVICE_SET_WEATHER_LOCATION = "set_weather_location"
SERVICE_ADD_ALARM = "add_alarm"
SERVICE_UPDATE_ALARM = "update_alarm"
SERVICE_DELETE_ALARM = "delete_alarm"
SERVICE_UPDATE_ALARM = "update_alarm"
SERVICE_SET_MAIN_DISPLAY_TEXT = "set_main_display_text"
SERVICE_SET_MINI_DISPLAY_NUMBER = "set_mini_display_number"
SERVICE_SET_RAINBOW_MODE = "set_rainbow_mode"
SERVICE_ACTIVATE_LIGHT_BAR_BLINK = "activate_light_bar_blink"
SERVICE_ACTIVATE_LIGHT_BAR_COMET = "activate_light_bar_comet"
SERVICE_ACTIVATE_LIGHT_BAR_PULSE = "activate_light_bar_pulse"
SERVICE_ACTIVATE_LIGHT_BAR_SET = "activate_light_bar_set"
SERVICE_ACTIVATE_LIGHT_BAR_SET_EACH = "activate_light_bar_set_each"
SERVICE_ACTIVATE_LIGHT_BAR_SWEEP = "activate_light_bar_sweep"

# Bridge (open firmware) data in the coordinator: coordinator.data[ATTR_BRIDGE] is GET /<dsn>/bridge
ATTR_BRIDGE = "bridge"
ATTR_TOPIC = "topic"
ATTR_DATA = "data"

EVENT_BUTTON_EVENT = (
    f"{DOMAIN}_button_event"  # every press/release/hold/repeat/long_press
)
EVENT_ALARM_EVENT = f"{DOMAIN}_alarm_event"  # ring / snooze / dismiss / timeout
EVENT_VOICE_REQUEST = f"{DOMAIN}_voice_request"  # MIC button asked for a session

SERVICE_VOICE_SAY = "voice_say"
SERVICE_ALARM_SNOOZE = "alarm_snooze"
SERVICE_ALARM_DISMISS = "alarm_dismiss"
SERVICE_ALARM_RING = "alarm_ring"

# Options (config entry options): feed a Home Assistant weather entity to the device's display
CONF_WEATHER_ENTITY = "weather_entity"
CONF_WEATHER_SCALE = "weather_scale"  # "F", "C" or "auto" (the entity's unit)
ALEXA_UNIQUE_ID_SUFFIXES = (
    "_alexa",
    "_alexa_tap_to_talk_tone",
    "_alexa_wake_word_tone",
    "_ascending_alarms",
)

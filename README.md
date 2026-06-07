# Beko AC IR Control — Home Assistant Integration

This is a heavy WIP for Beko 31225/30925 control with IR-blaster Moes UFO-R11 (Zigbee).
Two problems are solved 

  * Encode logical state into IR-blaster format
  * Intergrate solution with HA

Disclamer. I use a lot of AI in this project, you will see artefacts of it. That's just as life is now. 

## Требования

- Home Assistant с интеграцией MQTT
- Zigbee2MQTT
- IR-бластер Moes UFO-R11 (Zigbee, model TS1201) — по одному на каждый кондей
- pyscript custom component (`hacs` или вручную)

## Файлы

| Файл | Назначение |
|------|-----------|
| `beko_ir.py` | pyscript — генерация IR-фреймов и MQTT-триггеры |
| `packages/beko.yaml` | HA entities: кнопки, переключатели, input_number |
| `configuration.yaml` | Пример configuration.yaml с подключением пакета |
| `lovelace-beko.yaml` | Lovelace dashboard для двух кондеев |

## Установка

### 1. Установи pyscript

Если не установлен — через HACS: `Home Assistant Community Store → Integrations → pyscript`.

Или вручную: склонируй `custom-components/pyscript` в `/config/custom_components/pyscript`.

### 2. Задеплой скрипт

```bash
scp ir/beko_ir.py <ha-host>:/path/to/config/pyscript/beko_ir.py
```

Например:
```bash
scp ir/beko_ir.py 192.168.1.100:~/smarthome/homeassistant/config/pyscript/beko_ir.py
```

### 3. Создай папку packages и скопируй пакет

На сервере HA:
```bash
mkdir -p /path/to/config/packages
cp beko.yaml /path/to/config/packages/beko.yaml
```

### 4. Обнови configuration.yaml

Добавь в `configuration.yaml`:

```yaml
pyscript:
  allow_all_imports: true
  hass_is_global: true

homeassistant:
  packages:
    beko: !include packages/beko.yaml
```

Если `homeassistant:` уже есть — добавь только `packages:` внутрь.

### 5. Настрой устройства в beko_ir.py

В начале `beko_ir.py` найди словарь `DEVICES` и укажи правильные имена IR-бластеров из Zigbee2MQTT:

```python
DEVICES = {
    "kabinet": {
        "ir_topic":     "zigbee2mqtt/Black Box IR/set",
        "input_number": "input_number.beko_kabinet_temp",
    },
    "salon": {
        "ir_topic":     "zigbee2mqtt/Plugged IR/set",
        "input_number": "input_number.beko_salon_temp",
    },
}
```

Имя IR-бластера (`Black Box IR`, `Plugged IR`) должно совпадать с `friendly_name` устройства в Zigbee2MQTT.

### 6. Перезапусти Home Assistant

```bash
docker restart homeassistant
```

### 7. Добавь Lovelace dashboard

В HA: `Settings → Dashboards → Add Dashboard`.

Открой новый дашборд → три точки → `Edit Dashboard` → `Raw configuration editor`.

Вставь содержимое `lovelace-beko.yaml`.

## MQTT топики

Формат: `beko/<device>/set/<command>`

| Топик | Payload | Описание |
|-------|---------|----------|
| `beko/kabinet/set/mode` | `off` / `cool` / `heat` | Режим |
| `beko/kabinet/set/temp_up` | `1` | Температура +1°C |
| `beko/kabinet/set/temp_down` | `1` | Температура −1°C |
| `beko/kabinet/set/fan_mode` | `1`–`5` | Скорость вентилятора |
| `beko/kabinet/set/swing` | `pos1`–`pos6`, `auto` | Позиция жалюзи |
| `beko/kabinet/set/display` | `toggle` | Подсветка дисплея |
| `beko/kabinet/set/turbo` | `on` / `off` | Турбо режим |

Для `salon` — аналогично с префиксом `beko/salon/set/`.

## Добавление нового кондея

1. Добавь устройство в `DEVICES` в `beko_ir.py`
2. Добавь entities в `packages/beko.yaml` по аналогии с `kabinet` / `salon`
3. Добавь карточку в `lovelace-beko.yaml`
4. Задеплой `beko_ir.py`, скопируй обновлённый `packages/beko.yaml`, перезапусти HA

## Отладка

Проверить что pyscript загрузился:
```bash
docker logs homeassistant 2>&1 | grep beko
```

Мониторинг MQTT:
```bash
mosquitto_sub -h <ha-host> -p 1883 -t 'beko/#' -v
```

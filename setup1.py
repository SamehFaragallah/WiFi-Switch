import requests
import json
from config import CONFIG

apiHeader = {
    'Authorization-Token': CONFIG['device']['authorization_token']
}

data = {
    'name': CONFIG['device']['name'],
    'description': CONFIG['device']['description'],
    'action': 'addNewDevice'
}


res = requests.post('https://rock.lcbcchurch.com/Webhooks/Lava.ashx/WiFiSwitchAPI', data=data, headers=apiHeader)

print(json.dumps(res.json(), indent=2))

CONFIG['device']['device_id'] = res.json().get('deviceId', '')


def format_python_value(value, indent_level=0):
    """
    Format a Python value as valid Python code string.
    Handles dictionaries, lists, strings, booleans, None, numbers.
    """
    indent = '    ' * indent_level
    next_indent = '    ' * (indent_level + 1)
    
    if isinstance(value, dict):
        if not value:
            return '{}'
        items = []
        for k, v in value.items():
            key_str = f"'{k}'" if isinstance(k, str) else str(k)
            val_str = format_python_value(v, indent_level + 1)
            items.append(f"{next_indent}{key_str}: {val_str}")
        return '{\n' + ',\n'.join(items) + f'\n{indent}}}'
    elif isinstance(value, list):
        if not value:
            return '[]'
        items = []
        for item in value:
            item_str = format_python_value(item, indent_level + 1)
            items.append(f"{next_indent}{item_str}")
        return '[\n' + ',\n'.join(items) + f'\n{indent}]'
    elif isinstance(value, str):
        # Escape single quotes in strings
        escaped = value.replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
        return f"'{escaped}'"
    elif isinstance(value, bool):
        return 'True' if value else 'False'
    elif value is None:
        return 'None'
    elif isinstance(value, (int, float)):
        return str(value)
    else:
        # Fallback for other types - convert to string representation
        return repr(value)


def save_config_to_file(config_dict, filename='config.py'):
    """Save config dictionary to a Python file with proper formatting"""
    with open(filename, 'w') as f:
        f.write('CONFIG = ')
        f.write(format_python_value(config_dict))
        f.write('\n')

save_config_to_file(CONFIG, 'config.py')
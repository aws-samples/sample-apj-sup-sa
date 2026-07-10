import json
from collections import Counter


def count_keys_by_type(json_file_path):
    """
    Count the number of keys grouped by key-types from a JSON file.

    Args:
        json_file_path (str): Path to the JSON file
    """

    # Read the JSON file
    with open(json_file_path, 'r') as file:
        data = json.load(file)

    # Extract matched_keys
    matched_keys = data['data']['matched_keys']

    # Count keys by type
    key_type_counts = Counter()

    for key_entry in matched_keys:
        if 'attributes' in key_entry and 'key-type' in key_entry['attributes']:
            key_type = key_entry['attributes']['key-type']
            key_type_counts[key_type] += 1
        else:
            key_type_counts['unknown'] += 1

    # Print results
    print(f"Key Type Counts:")
    print("-" * 20)
    for key_type, count in sorted(key_type_counts.items()):
        print(f"{key_type}: {count}")
    print("-" * 20)
    print(f"Total: {sum(key_type_counts.values())}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 key-type-count.py <json_file_path>")
        print("Example: python3 key-type-count.py private_keys.json")
        sys.exit(1)

    json_file_path = sys.argv[1]
    count_keys_by_type(json_file_path)

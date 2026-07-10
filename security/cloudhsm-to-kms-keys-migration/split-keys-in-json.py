
import json
import os
from typing import Dict, List, Any
from collections import defaultdict


def split_json_by_key_type(input_file_path: str, output_dir: str = "split_output", max_keys_per_file: int = 50):
    """
    Split a JSON file containing matched keys by key-type, maintaining structure
    and limiting each output file to max_keys_per_file keys.

    Args:
        input_file_path (str): Path to the input JSON file
        output_dir (str): Directory where split files will be saved
        max_keys_per_file (int): Maximum number of keys per output file (default: 50)
    """

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Read the input JSON file
    try:
        with open(input_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        print(f"Error: Input file '{input_file_path}' not found.")
        return
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format in input file. {e}")
        return

    # Extract matched_keys from the data
    if 'data' not in data or 'matched_keys' not in data['data']:
        print(
            "Error: Input JSON doesn't contain the expected structure (data.matched_keys).")
        return

    matched_keys = data['data']['matched_keys']

    # Group keys by key-type
    keys_by_type = defaultdict(list)

    for key_entry in matched_keys:
        # Extract key-type from attributes
        if 'attributes' in key_entry and 'key-type' in key_entry['attributes']:
            key_type = key_entry['attributes']['key-type']
            keys_by_type[key_type].append(key_entry)
        else:
            # Handle keys without key-type (put them in 'unknown' category)
            keys_by_type['unknown'].append(key_entry)

    # Split each key-type group into multiple files if needed
    file_counter = 0
    summary = {}

    for key_type, keys in keys_by_type.items():
        total_keys_for_type = len(keys)
        files_needed = (total_keys_for_type +
                        max_keys_per_file - 1) // max_keys_per_file

        summary[key_type] = {
            'total_keys': total_keys_for_type,
            'files_created': files_needed
        }

        for file_index in range(files_needed):
            # Calculate start and end indices for this chunk
            start_idx = file_index * max_keys_per_file
            end_idx = min(start_idx + max_keys_per_file, total_keys_for_type)

            # Get the chunk of keys for this file
            keys_chunk = keys[start_idx:end_idx]

            # Create the output structure maintaining the original format
            output_data = {
                "error_code": data.get("error_code", 0),
                "data": {
                    "matched_keys": keys_chunk,
                    "total_key_count": len(keys_chunk),
                    "returned_key_count": len(keys_chunk),
                    "key_type_filter": key_type,
                    "file_part": file_index + 1,
                    "total_parts": files_needed,
                    "original_total_count": data['data'].get('total_key_count', 'unknown')
                }
            }

            # Generate output filename
            if files_needed == 1:
                output_filename = f"{key_type}_keys.json"
            else:
                output_filename = f"{key_type}_keys_part_{file_index + 1:03d}.json"

            output_path = os.path.join(output_dir, output_filename)

            # Write the output file
            try:
                with open(output_path, 'w', encoding='utf-8') as output_file:
                    json.dump(output_data, output_file,
                              indent=2, ensure_ascii=False)

                file_counter += 1
                print(f"Created: {output_filename} ({len(keys_chunk)} keys)")

            except Exception as e:
                print(f"Error writing file '{output_filename}': {e}")

    # Print summary
    print(f"\n--- Split Summary ---")
    print(f"Total files created: {file_counter}")
    print(f"Output directory: {output_dir}")
    print("\nBreakdown by key-type:")
    for key_type, info in summary.items():
        print(
            f"  {key_type}: {info['total_keys']} keys → {info['files_created']} file(s)")


def main():
    """
    Example usage of the split function
    """
    # Example usage
    input_file = "private_keys.json"
    output_directory = "split_keys_output"
    max_keys = 50

    print(f"Splitting JSON file: {input_file}")
    print(f"Max keys per file: {max_keys}")
    print(f"Output directory: {output_directory}")
    print("-" * 50)

    split_json_by_key_type(input_file, output_directory, max_keys)


if __name__ == "__main__":
    main()

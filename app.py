import os
import json
import pandas as pd
from nicegui import ui
from dateutil.parser import parse
from utils import (
    generate_identifier,
    is_duplicate,
    detect_and_categorize,
    prevent_duplicates,
    load_json_data,
    save_json_data
)

JSON_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

class DataProcessorApp:
    def __init__(self):
        self.file_path = None
        self.progress_bar = None
        self.status_label = None
        self.file_preview = None
        self.data_category = None
        self.output_preview = None

    def build(self):
        ui.colors(primary='#007bff')

        with ui.column().style('gap: 10px; padding: 10px; margin: 0 auto; max-width: 95%'):
            ui.label("Data Processor").style('font-size: 18px; font-weight: bold; text-align: center')

            self.status_label = ui.label("No file uploaded").style('font-size: 12px; color: gray; text-align: center')

            self.file_upload = ui.upload(
                on_upload=self.handle_upload,
                label='Upload CSV',
                auto_upload=True
            ).props('accept=".csv"').classes('w-full')

            self.file_preview = ui.table(columns=[{"name": col, "label": col, "field": col} for col in []],
                                         rows=[]).classes('hidden w-full')

            self.process_button = ui.button("Process Data", on_click=self.process_data).style(
                'width: 100%; padding: 8px; border-radius: 4px'
            ).bind_visibility_from(self.file_upload, 'value').bind_visibility_from(self, 'data_category')

            self.progress_bar = ui.linear_progress(value=0.0).style('width: 100%; height: 6px; margin-top: 5px')

            self.output_preview = ui.label().classes('hidden')

    def handle_upload(self, e):
        if not e.content:
            self.show_error_dialog("No file uploaded.")
            self.reset_ui()
            return

        filename = e.name

        if not filename.endswith('.csv'):
            self.show_error_dialog("Please select a valid CSV file.")
            self.reset_ui()
            return

        self.file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

        with open(self.file_path, 'wb') as f:
            while True:
                chunk = e.content.read(1024)
                if not chunk:
                    break
                f.write(chunk)

        self.status_label.text = "File uploaded: " + filename

        try:
            df = pd.read_csv(self.file_path)

            # Update table columns and data
            self.file_preview.columns = [col for col in df.columns]
            self.file_preview.rows = df.head(4).to_dict('records')
            self.file_preview.classes('block')

            self.data_category = self.detect_data_category(df.columns)
            if self.data_category:
                self.status_label.text += f" (Detected category: {self.data_category})"
                self.process_button.visible = True
            else:
                self.show_error_dialog("Unable to automatically detect the data category. Please ensure your CSV file follows the specified format.")
                self.reset_ui()

        except Exception as e:
            self.show_error_dialog(f"Error reading CSV file: {e}")
            self.reset_ui()

    def detect_data_category(self, columns):
        categories = {
            "Messenger": ["Time", "Sender", "Text"],
            "SMS": ["SMS Type", "Time", "From/To", "Text", "Location"],
            "Calls": ["Call Type", "Time", "From/To", "Duration", "Location"],
            "Keylog": ["Application", "Time", "Text"],
            "Contacts": ["Name", "Phone Number", "Email Id", "Last Contacted"],
            "Installed Apps": ["Application Name", "Package Name", "Installed Date"]
        }

        for category, required_columns in categories.items():
            if all(col in columns for col in required_columns):
                return category

        return None

    async def process_data(self):
        if not self.file_path:
            self.show_error_dialog("No file uploaded.")
            return
        if not self.data_category:
            self.show_error_dialog("Data category not detected or selected. Please ensure your CSV file follows the specified format.")
            return

        try:
            self.progress_bar.set_value(0.25)
            # await ui.run_javascript('document.documentElement.scrollTop = document.documentElement.scrollHeight;')  # Removed due to auto-index issue

            # Load existing JSON data (if any)
            existing_data = load_json_data(JSON_DB_PATH)

            # Process the CSV data
            df = pd.read_csv(self.file_path)
            csv_data = df.to_dict(orient='records')

            # Standardize time formats
            for row in csv_data:
                for field in ['Time', 'Last Contacted', 'Installed Date']:
                    if field in row:
                        try:
                            row[field] = parse(row[field]).strftime('%Y-%m-%dT%H:%M:%S')
                        except ValueError:
                            row[field] = None 

            # Process full data
            categorized_data = detect_and_categorize(csv_data)
            final_data = prevent_duplicates(categorized_data)

            # Merge with existing data
            for category, entries in final_data.items():
                if category not in existing_data:
                    existing_data[category] = []
                existing_data[category].extend(entries)

            # Update unique identifiers
            global unique_identifiers 
            unique_identifiers.update({
                name_or_number: identifier 
                for name_or_number, identifier in existing_data.get('identifiers', {}).items()
            })

            # Save updated data
            save_json_data(existing_data, JSON_DB_PATH)

            self.progress_bar.set_value(1.0)

            self.output_preview.text = f"Output (first 5 entries):\n {json.dumps(existing_data, indent=4)}" 
            self.output_preview.classes('block')

            self.show_success_dialog("Data processed and saved successfully!")
        except Exception as e:
            self.show_error_dialog(f"An error occurred during processing: {e}")
            self.progress_bar.set_value(0)

    def assign_unique_identifiers(self, processed_data, category, existing_identifiers):
        """
        Assigns unique identifiers to senders/contacts in the data.
        """

        if existing_identifiers is None:
            existing_identifiers = {}
        next_id = max(int(id.split("_")[1]) for id in existing_identifiers.values()) + 1 if existing_identifiers else 1

        for entry in processed_data:
            if category in ["Messenger", "SMS", "Calls"]:
                sender_field = "Sender" if category == "Messenger" else "From/To"
                sender = entry[sender_field]
            elif category == "Contacts":
                sender = entry["Name"]
            else:  # For Keylog and Installed Apps, no identifier is needed
                continue

            if sender not in existing_identifiers:
                existing_identifiers[sender] = f"ID_{next_id:03d}"
                next_id += 1
            entry["identifier"] = existing_identifiers[sender]

        return processed_data, existing_identifiers

    def prevent_duplicate_entries(self, processed_data, category):
        """
        Removes duplicate entries from the data based on unique fields for each category
        """

        unique_fields = {
            "Messenger": ["Time", "Sender", "Text"],
            "SMS": ["Time", "From/To", "Text"],
            "Calls": ["Time", "From/To", "Duration"],
            "Keylog": ["Application", "Time", "Text"],
            "Contacts": ["Name", "Phone Number"],
            "Installed Apps": ["Application Name", "Package Name"]
    }

    seen = set()
    unique_data = []
    for entry in processed_data:
        key = tuple(entry[field] for field in unique_fields[category])
        if key not in seen:
            seen.add(key)
            unique_data.append(entry)

    return unique_data

    def show_error_dialog(self, message):
        ui.dialog().with_title('Error').with_text(message).open()

    def show_success_dialog(self, message):
        ui.dialog().with_title('Success').with_text(message).open()

    def reset_ui(self):
        self.file_upload.clear()
        self.status_label.text = "No file uploaded"
        self.file_preview.classes('hidden')
        self.output_preview.classes('hidden')
        self.progress_bar.set_value(0)


# Create and run the NiceGUI app
app_instance = DataProcessorApp()
app_instance.build()
ui.run(host="0.0.0.0", port=8080)
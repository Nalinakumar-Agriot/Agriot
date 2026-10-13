import os
import json
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.superadmin.models import Country, State, City

class Command(BaseCommand):
    help = 'Seeds database with countries, states, and cities from JS files.'

    def parse_js_to_json(self, file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # The content has `var_name =[` and ends with `];` or `module.exports...`
        start_idx = content.find('[')
        end_idx = content.rfind(']') + 1
        json_str = content[start_idx:end_idx]
        
        return json.loads(json_str)

    def handle(self, *args, **kwargs):
        base_dir = settings.BASE_DIR
        countries_file = os.path.join(base_dir, 'countries.js')
        states_file = os.path.join(base_dir, 'states.js')
        cities_file = os.path.join(base_dir, 'cities.js')
        
        self.stdout.write("Loading countries...")
        countries_data = self.parse_js_to_json(countries_file)
        country_objects = []
        for c in countries_data:
            country_objects.append(Country(
                id=c['id'],
                name=c['name'],
                sort_name=c.get('sort_name', ''),
                phone_code=c.get('phone_code', ''),
                status=c.get('status', '0')
            ))
        Country.objects.bulk_create(country_objects, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(country_objects)} countries."))

        self.stdout.write("Loading states...")
        states_data = self.parse_js_to_json(states_file)
        state_objects = []
        for s in states_data:
            state_objects.append(State(
                id=s['id'],
                country_id=s['country_id'],
                name=s['name'],
                status=s.get('status', '0')
            ))
        State.objects.bulk_create(state_objects, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(state_objects)} states."))

        self.stdout.write("Loading cities...")
        cities_data = self.parse_js_to_json(cities_file)
        
        # Bulk create cities in chunks since there are 48,000+ records
        city_objects = []
        for c in cities_data:
            city_objects.append(City(
                id=c['id'],
                state_id=c['state_id'],
                name=c['name'],
                cid=c.get('cid', ''),
                status=c.get('status', '0')
            ))
        
        City.objects.bulk_create(city_objects, batch_size=1000, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(city_objects)} cities."))
        
        self.stdout.write(self.style.SUCCESS("Successfully seeded all locations!"))

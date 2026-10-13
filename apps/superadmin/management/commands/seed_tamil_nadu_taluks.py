import json
from django.core.management.base import BaseCommand
from django.db import connection
from apps.superadmin.models import Country, State, City, Taluk

class Command(BaseCommand):
    help = 'Seeds Tamil Nadu districts (cities) and taluks'

    def handle(self, *args, **kwargs):
        # Fix sequences for Postgres
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT setval(pg_get_serial_sequence('superadmin_city', 'id'), coalesce(max(id),0) + 1, false) FROM superadmin_city;")
                cursor.execute("SELECT setval(pg_get_serial_sequence('superadmin_taluk', 'id'), coalesce(max(id),0) + 1, false) FROM superadmin_taluk;")

        # Ensure India exists
        country, _ = Country.objects.get_or_create(name='India', defaults={'status': '1'})
        
        # Ensure Tamil Nadu exists
        state, _ = State.objects.get_or_create(name='Tamil Nadu', defaults={'country': country, 'status': '1'})

        # Full data for 38 Tamil Nadu Districts and Taluks
        tn_data = {
            "Ariyalur": ["Ariyalur", "Sendurai", "Udayarpalayam", "Andimadam"],
            "Chengalpattu": ["Chengalpattu", "Tambaram", "Pallavaram", "Vandalur", "Thiruporur", "Tirukalukundram", "Maduranthakam", "Cheyyur"],
            "Chennai": ["Aminjikarai", "Ayanavaram", "Egmore", "Guindy", "Mambalam", "Mylapore", "Perambur", "Purasawalkam", "Tondiarpet", "Velachery", "Madhavaram", "Tiruvottiyur", "Alandur", "Sholinganallur", "Ambattur", "Maduravoyal"],
            "Coimbatore": ["Coimbatore North", "Coimbatore South", "Mettupalayam", "Pollachi", "Valparai", "Sulur", "Kinathukadavu", "Annur", "Perur", "Madukkarai", "Anamalai"],
            "Cuddalore": ["Cuddalore", "Chidambaram", "Vriddhachalam", "Panruti", "Kurinjipadi", "Kattumannarkoil", "Tittakudi", "Bhuvanagiri", "Srimushnam", "Veppur"],
            "Dharmapuri": ["Dharmapuri", "Harur", "Palacode", "Pennagaram", "Pappireddipatti", "Karimangalam", "Nallampalli"],
            "Dindigul": ["Dindigul", "Palani", "Kodaikanal", "Vedasandur", "Nilakkottai", "Natham", "Athoor", "Gujiliamparai", "Oddanchatram"],
            "Erode": ["Erode", "Bhavani", "Gobichettipalayam", "Sathyamangalam", "Perundurai", "Anthiyur", "Modakkurichi", "Kodumudi", "Thalavadi", "Nambiyur"],
            "Kallakurichi": ["Kallakurichi", "Sankarapuram", "Chinnasalem", "Ulundurpet", "Tirukkoyilur", "Kalvarayan Hills"],
            "Kanchipuram": ["Kanchipuram", "Sriperumbudur", "Uthiramerur", "Walajabad", "Kundrathur"],
            "Kanyakumari": ["Agastheeswaram", "Kalkulam", "Vilavancode", "Thovalai", "Thiruvattar", "Killiyur"],
            "Karur": ["Karur", "Kulithalai", "Krishnarayapuram", "Aravakurichi", "Manmangalam", "Pugalur", "Kadavur"],
            "Krishnagiri": ["Krishnagiri", "Hosur", "Pochampalli", "Uthangarai", "Denkanikottai", "Bargur", "Sulagiri", "Anchetty"],
            "Madurai": ["Madurai North", "Madurai South", "Melur", "Thirumangalam", "Peraiyur", "Usilampatti", "Vadipatti", "Kalligudi", "Madurai East", "Madurai West"],
            "Mayiladuthurai": ["Mayiladuthurai", "Sirkazhi", "Kuthalam", "Tharangambadi"],
            "Nagapattinam": ["Nagapattinam", "Kilvelur", "Thirukuvalai", "Vedaranyam"],
            "Namakkal": ["Namakkal", "Rasipuram", "Tiruchengode", "Paramathi Velur", "Kolli Hills", "Sendamangalam", "Kumarapalayam", "Mohanur"],
            "Nilgiris": ["Udhagamandalam", "Coonoor", "Kotagiri", "Gudalur", "Pandalur", "Kundah"],
            "Perambalur": ["Perambalur", "Kunnam", "Veppanthattai", "Alathur"],
            "Pudukkottai": ["Pudukkottai", "Alangudi", "Aranthangi", "Avadaiyarkoil", "Gandarvakottai", "Illuppur", "Karambakkudi", "Kulathur", "Manamelkudi", "Ponnamaravathi", "Thirumayam", "Viralimalai"],
            "Ramanathapuram": ["Ramanathapuram", "Rameswaram", "Tiruvadanai", "Paramakudi", "Kamuthi", "Mudukulathur", "Kadaladi", "Kilakarai", "Rajasingamangalam"],
            "Ranipet": ["Ranipet", "Arakkonam", "Arcot", "Walajah", "Nemili", "Sholinghur", "Kalavai"],
            "Salem": ["Salem", "Attur", "Edappadi", "Gangavalli", "Kadaiyampatti", "Mettur", "Omalur", "Pethanaickenpalayam", "Sankari", "Vazhapadi", "Yercaud"],
            "Sivaganga": ["Sivaganga", "Karaikudi", "Devakottai", "Tiruppattur", "Manamadurai", "Ilayangudi", "Thirupuvanam", "Kalaiyarkoil", "Singampunari"],
            "Tenkasi": ["Tenkasi", "Shenkottai", "Kadayanallur", "Sivagiri", "Veerakeralampudur", "Sankarankoil", "Thiruvenkatam", "Alangulam"],
            "Thanjavur": ["Thanjavur", "Kumbakonam", "Papanasam", "Pattukkottai", "Peravurani", "Orathanadu", "Thiruvidaimarudur", "Thiruvaiyaru", "Budalur"],
            "Theni": ["Theni", "Periyakulam", "Andipatti", "Uthamapalayam", "Bodinayakanur"],
            "Thoothukudi": ["Thoothukudi", "Tiruchendur", "Sathankulam", "Srivaikuntam", "Ottapidaram", "Kovilpatti", "Ettayapuram", "Vilathikulam", "Kayathar", "Eral"],
            "Tiruchirappalli": ["Lalgudi", "Manachanallur", "Manapparai", "Musiri", "Srirangam", "Thiruverumbur", "Tiruchirappalli West", "Tiruchirappalli East", "Turaiyur"],
            "Tirunelveli": ["Tirunelveli", "Palayamkottai", "Ambasamudram", "Nanguneri", "Radhapuram", "Manur", "Cheranmahadevi", "Thisayanvilai"],
            "Tirupathur": ["Tirupathur", "Vaniyambadi", "Ambur", "Natrampalli"],
            "Tiruppur": ["Tiruppur North", "Tiruppur South", "Avinashi", "Palladam", "Kangeyam", "Dharapuram", "Udumalpet", "Madathukulam", "Uthukuli"],
            "Tiruvallur": ["Tiruvallur", "Poonamallee", "Ponneri", "Gummidipoondi", "Uthukkottai", "Tiruttani", "Pallipattu", "R.K. Pet", "Avadi"],
            "Tiruvannamalai": ["Tiruvannamalai", "Arni", "Cheyyar", "Vandavasi", "Polur", "Chengam", "Thandarampattu", "Kalasapakkam", "Chetpet", "Kilpennathur", "Vembakkam", "Jamunamarathur"],
            "Tiruvarur": ["Tiruvarur", "Mannargudi", "Thiruthuraipoondi", "Needamangalam", "Nannilam", "Valangaiman", "Kodavasal", "Koothanallur"],
            "Vellore": ["Vellore", "Katpadi", "Gudiyatham", "Pernambut", "K.V. Kuppam", "Anaicut"],
            "Viluppuram": ["Viluppuram", "Tindivanam", "Gingee", "Vanur", "Marakkanam", "Vikravandi", "Kandachipuram", "Tiruvennainallur", "Melmalaiyanur"],
            "Virudhunagar": ["Virudhunagar", "Aruppukkottai", "Srivilliputhur", "Rajapalayam", "Sattur", "Sivakasi", "Kariapatti", "Tiruchuli", "Vembakottai", "Watrap"]
        }

        for district_name, taluks in tn_data.items():
            city, created = City.objects.get_or_create(name=district_name, state=state, defaults={'status': '1'})
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created District: {district_name}"))
            
            for taluk_name in taluks:
                taluk, t_created = Taluk.objects.get_or_create(name=taluk_name, city=city, defaults={'status': '1'})
                if t_created:
                    self.stdout.write(self.style.SUCCESS(f"  Created Taluk: {taluk_name}"))

        self.stdout.write(self.style.SUCCESS('Successfully seeded Tamil Nadu districts and taluks.'))

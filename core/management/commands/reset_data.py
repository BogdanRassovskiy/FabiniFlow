from django.core.management.base import BaseCommand

from core import storage


class Command(BaseCommand):
    help = "Obnoví živá JSON data z výchozích seed dat (data/seed → data/runtime)."

    def handle(self, *args, **options):
        collections = storage.reset_from_seed()
        self.stdout.write(self.style.SUCCESS(f"Obnoveno: {', '.join(collections)}"))

# Generated manually on 2025-10-06
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='EducationItem',
            fields=[
                ('id', models.AutoField(auto_created=True,
                                        primary_key=True,
                                        serialize=False,
                                        verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('file_type', models.CharField(
                    choices=[('pdf', 'PDF File'), ('aparat', 'Aparat Link')],
                    max_length=10
                )),
                ('file', models.FileField(
                    blank=True, null=True, upload_to='education_pdfs/'
                )),
                ('url', models.URLField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]

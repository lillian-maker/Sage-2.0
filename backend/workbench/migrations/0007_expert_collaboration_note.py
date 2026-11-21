from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0006_taskstep_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='expert',
            name='collaboration_note',
            field=models.CharField(blank=True, max_length=60, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0004_inboxitem_channel_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='authorizationrequest',
            name='title',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
    ]

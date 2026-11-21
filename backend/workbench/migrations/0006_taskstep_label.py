from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0005_authorizationrequest_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskstep',
            name='label',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]

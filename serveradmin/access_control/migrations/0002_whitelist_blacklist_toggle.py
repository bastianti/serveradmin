# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2019-07-09 13:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('access_control', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesscontrolgroup',
            name='is_whitelist',
            field=models.BooleanField(default=True, help_text='If checked, the attribute list is treated as a whitelist, otherwise it is treated as a blacklist.'),
        ),
        migrations.AlterField(
            model_name='accesscontrolgroup',
            name='attributes',
            field=models.ManyToManyField(blank=True, help_text="Note that you currently can't select the special attributes like object_id, hostname, servertype or intern_ip here.", related_name='access_control_groups', to='serverdb.Attribute'),
        ),
    ]

# Generated by Django 4.2 on 2024-01-09 10:50

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("abuse", "0020_alter_abusereport_addon_install_method_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="abusereport",
            name="state",
        ),
        migrations.AddField(
            model_name="abusereport",
            name="appeal_date",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="cinderjob",
            name="appeal_job",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="appealed_job",
                to="abuse.cinderjob",
            ),
        ),
        migrations.AlterField(
            model_name="abusereport",
            name="reason",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
                    (None, "None"),
                    (1, "Damages computer and/or data"),
                    (2, "Creates spam or advertising"),
                    (
                        3,
                        "Changes search / homepage / new tab page without informing user",
                    ),
                    (5, "Doesn’t work, breaks websites, or slows Firefox down"),
                    (6, "Hateful, violent, or illegal content"),
                    (7, "Pretends to be something it’s not"),
                    (9, "Wasn't wanted / impossible to get rid of"),
                    (
                        11,
                        "DSA: It contains hateful, violent, deceptive, or other inappropriate content",
                    ),
                    (
                        12,
                        "DSA: It violates the law or contains content that violates the law",
                    ),
                    (13, "DSA: It violates Mozilla's Add-on Policies"),
                    (14, "DSA: Something else"),
                    (
                        20,
                        "Feedback: It does not work, breaks websites, or slows down Firefox",
                    ),
                    (21, "Feedback: It's spam"),
                    (127, "Other"),
                ],
                default=None,
                null=True,
            ),
        ),
        migrations.DeleteModel(
            name="CinderJobAppeal",
        ),
    ]

"""Phase 8.1 — engineering_units master + override-capable tag column

Creates a global master list of engineering units with industry-comprehensive
seed data covering Chemical, Oil & Gas, Water/Wastewater, and Pharma. Extends
the tags table to reference the master via FK while keeping the existing
engineering_unit text column as a per-tag override escape hatch.

Design:
  engineering_units            — the global master, ~130 seeded entries
  tags.engineering_unit_id     — preferred reference (FK)
  tags.engineering_unit        — repurposed as override (still text)
  CHECK constraint             — exactly zero or one of the two may be set

Display resolver (in API/UI):
  if engineering_unit_id is not null:  show master.code
  elif engineering_unit is not null:   show engineering_unit  (override)
  else:                                no unit

Existing data: every tag with a non-null engineering_unit text is matched
against the seed master case-insensitively (on both code and label). Matches
move to FK; non-matches stay as text overrides. Nothing is lost.

is_system flag on seeded rows: lets users disable but not delete the seed
library, so they can't accidentally wipe out the foundation. They can freely
add and delete their own (non-system) entries.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_engineering_units"
down_revision: Union[str, None] = "0004_cumulative_counters"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Seed data — organized by quantity_kind for the grouped dropdown UX.
# Format: (code, label, quantity_kind, description_or_None)
# ---------------------------------------------------------------------------
SEED_UNITS: list[tuple[str, str, str, str | None]] = [
    # --- Temperature ---------------------------------------------------------
    ("°C",        "Celsius",                          "temperature", "Standard SI temperature scale"),
    ("°F",        "Fahrenheit",                       "temperature", "US customary temperature scale"),
    ("K",         "Kelvin",                           "temperature", "Absolute temperature scale (SI base unit)"),
    ("°R",        "Rankine",                          "temperature", "Absolute Fahrenheit-based scale, rare US oil & gas"),

    # --- Pressure ------------------------------------------------------------
    ("bar",       "bar",                              "pressure",    "Common metric pressure unit"),
    ("mbar",      "millibar",                         "pressure",    "Low-pressure metric unit"),
    ("Pa",        "Pascal",                           "pressure",    "SI pressure unit"),
    ("kPa",       "kilopascal",                       "pressure",    "Common SI-derived pressure unit"),
    ("MPa",       "megapascal",                       "pressure",    "High-pressure SI unit"),
    ("psi",       "pounds per square inch",           "pressure",    "US customary pressure"),
    ("psig",      "pounds per square inch gauge",     "pressure",    "Pressure relative to atmosphere"),
    ("psia",      "pounds per square inch absolute",  "pressure",    "Pressure relative to vacuum"),
    ("kg/cm²",    "kilograms per square centimeter",  "pressure",    "Common in India and Asia"),
    ("atm",       "atmospheres",                      "pressure",    "Standard atmospheric pressure"),
    ("torr",      "Torr",                             "pressure",    "Vacuum measurement, 1 atm = 760 torr"),
    ("mmHg",      "millimeters of mercury",           "pressure",    "Medical/vacuum pressure unit"),
    ("inH₂O",     "inches of water column",           "pressure",    "Low pressure, HVAC and stack draft"),
    ("mmH₂O",     "millimeters of water column",      "pressure",    "Low pressure metric equivalent"),
    ("inHg",      "inches of mercury",                "pressure",    "Vacuum measurement, US convention"),

    # --- Flow — Volumetric ---------------------------------------------------
    ("m³/h",      "cubic meters per hour",            "flow_volume", "Most common metric volumetric flow"),
    ("m³/min",    "cubic meters per minute",          "flow_volume", None),
    ("m³/s",      "cubic meters per second",          "flow_volume", "SI base unit"),
    ("L/h",       "liters per hour",                  "flow_volume", "Low-rate metric flow"),
    ("L/min",     "liters per minute",                "flow_volume", "Also called LPM"),
    ("L/s",       "liters per second",                "flow_volume", None),
    ("mL/min",    "milliliters per minute",           "flow_volume", "Lab and pharma scale"),
    ("gpm",       "US gallons per minute",            "flow_volume", "US customary, common in water"),
    ("gph",       "US gallons per hour",              "flow_volume", None),
    ("bbl/h",     "barrels per hour",                 "flow_volume", "Oil industry (US barrel = 159 L)"),
    ("bbl/d",     "barrels per day",                  "flow_volume", "Oil production rate"),
    ("ft³/min",   "cubic feet per minute",            "flow_volume", "Also CFM"),
    ("ft³/h",     "cubic feet per hour",              "flow_volume", None),
    ("MMSCFD",    "million standard cubic feet/day",  "flow_volume", "Gas industry, US convention"),
    ("SCFM",      "standard cubic feet per minute",   "flow_volume", "Gas at standard conditions"),
    ("Sm³/h",     "standard cubic meters per hour",   "flow_volume", "Gas at standard conditions (metric)"),
    ("Nm³/h",     "normal cubic meters per hour",     "flow_volume", "Gas at normal conditions (0 °C, 1 atm)"),
    ("MMSCMD",    "million std cubic meters per day", "flow_volume", "Indian gas industry convention"),

    # --- Flow — Mass ---------------------------------------------------------
    ("kg/h",      "kilograms per hour",               "flow_mass",   "Most common metric mass flow"),
    ("kg/min",    "kilograms per minute",             "flow_mass",   None),
    ("kg/s",      "kilograms per second",             "flow_mass",   "SI base mass flow"),
    ("t/h",       "tonnes per hour",                  "flow_mass",   "Heavy industrial mass flow"),
    ("t/d",       "tonnes per day",                   "flow_mass",   "Daily production rate"),
    ("g/s",       "grams per second",                 "flow_mass",   "Lab and pharma scale"),
    ("g/min",     "grams per minute",                 "flow_mass",   None),
    ("lb/h",      "pounds per hour",                  "flow_mass",   "US customary mass flow"),
    ("lb/min",    "pounds per minute",                "flow_mass",   None),

    # --- Level ---------------------------------------------------------------
    ("m",         "meters",                           "length",      "Standard SI length, level measurement"),
    ("mm",        "millimeters",                      "length",      None),
    ("cm",        "centimeters",                      "length",      None),
    ("in",        "inches",                           "length",      "US customary length"),
    ("ft",        "feet",                             "length",      "US customary length"),

    # --- Volume / Mass totalizers --------------------------------------------
    ("m³",        "cubic meters",                     "volume",      "Volume totalizer SI"),
    ("L",         "liters",                           "volume",      None),
    ("mL",        "milliliters",                      "volume",      "Lab and pharma scale"),
    ("bbl",       "barrels",                          "volume",      "Oil industry totalizer"),
    ("gal",       "US gallons",                       "volume",      None),
    ("ft³",       "cubic feet",                       "volume",      "US customary volume"),
    ("kg",        "kilograms",                        "mass",        "Mass totalizer SI"),
    ("g",         "grams",                            "mass",        None),
    ("mg",        "milligrams",                       "mass",        "Lab and pharma scale"),
    ("μg",        "micrograms",                       "mass",        "Pharma trace amounts"),
    ("t",         "tonnes (metric tons)",             "mass",        "1 t = 1000 kg"),
    ("lb",        "pounds",                           "mass",        "US customary mass"),

    # --- Energy --------------------------------------------------------------
    ("Wh",        "watt-hours",                       "energy",      None),
    ("kWh",       "kilowatt-hours",                   "energy",      "Common electrical energy"),
    ("MWh",       "megawatt-hours",                   "energy",      None),
    ("J",         "joules",                           "energy",      "SI energy"),
    ("kJ",        "kilojoules",                       "energy",      None),
    ("MJ",        "megajoules",                       "energy",      None),
    ("GJ",        "gigajoules",                       "energy",      "Large-scale heat/process energy"),
    ("BTU",       "British thermal units",            "energy",      None),
    ("MMBTU",     "million BTU",                      "energy",      "Gas custody-transfer energy"),
    ("therm",     "therms",                           "energy",      "Natural gas billing in some regions"),
    ("kcal",      "kilocalories",                     "energy",      None),
    ("cal",       "calories",                         "energy",      None),

    # --- Power ---------------------------------------------------------------
    ("W",         "watts",                            "power",       "SI power"),
    ("kW",        "kilowatts",                        "power",       None),
    ("MW",        "megawatts",                        "power",       None),
    ("HP",        "horsepower",                       "power",       "Motor power, US convention"),

    # --- Electrical ----------------------------------------------------------
    ("V",         "volts",                            "voltage",     "SI voltage"),
    ("mV",        "millivolts",                       "voltage",     "Sensor signal level"),
    ("kV",        "kilovolts",                        "voltage",     "High voltage"),
    ("A",         "amperes",                          "current",     "SI current"),
    ("mA",        "milliamperes",                     "current",     "Process signal (4–20 mA)"),
    ("kA",        "kiloamperes",                      "current",     None),
    ("Hz",        "hertz",                            "frequency",   "Frequency, also for vibration"),
    ("Ω",         "ohms",                             "resistance",  "Electrical resistance"),
    ("VA",        "volt-amperes",                     "power",       "Apparent power"),
    ("kVA",       "kilovolt-amperes",                 "power",       "Transformer rating"),
    ("VAR",       "volt-amperes reactive",            "power",       "Reactive power"),
    ("kVAR",      "kilovolt-amperes reactive",        "power",       None),
    ("pf",        "power factor",                     "dimensionless", "Ratio of real to apparent power, 0–1"),

    # --- Speed / Rotational --------------------------------------------------
    ("rpm",       "revolutions per minute",           "rotation",    "Motor / pump / turbine speed"),
    ("rps",       "revolutions per second",           "rotation",    None),
    ("rad/s",     "radians per second",               "rotation",    "Angular velocity SI"),
    ("m/s",       "meters per second",                "velocity",    "SI velocity"),
    ("km/h",      "kilometers per hour",              "velocity",    None),
    ("ft/s",      "feet per second",                  "velocity",    None),
    ("mph",       "miles per hour",                   "velocity",    "US convention"),

    # --- Time ----------------------------------------------------------------
    ("ms",        "milliseconds",                     "time",        None),
    ("s",         "seconds",                          "time",        "SI base unit of time"),
    ("min",       "minutes",                          "time",        None),
    ("h",         "hours",                            "time",        None),
    ("d",         "days",                             "time",        None),

    # --- Density -------------------------------------------------------------
    ("kg/m³",     "kilograms per cubic meter",        "density",     "SI density"),
    ("g/cm³",     "grams per cubic centimeter",       "density",     "Liquid density convention"),
    ("g/L",       "grams per liter",                  "density",     None),
    ("lb/ft³",    "pounds per cubic foot",            "density",     "US convention"),
    ("lb/gal",    "pounds per US gallon",             "density",     "Mud weight in oilfield"),

    # --- Concentration / Composition -----------------------------------------
    ("%",         "percent",                          "ratio",       "Generic percentage"),
    ("ppm",       "parts per million",                "concentration", "Trace concentration"),
    ("ppb",       "parts per billion",                "concentration", "Ultra-trace concentration"),
    ("mol/L",     "moles per liter",                  "concentration", "Molar concentration (molarity)"),
    ("mol/m³",    "moles per cubic meter",            "concentration", "SI molar concentration"),
    ("mg/L",      "milligrams per liter",             "concentration", "Water industry (TDS, BOD, COD)"),
    ("μg/L",      "micrograms per liter",             "concentration", "Trace contaminants in water"),
    ("mol%",      "mole percent",                     "concentration", "Gas analysis composition"),
    ("vol%",      "volume percent",                   "concentration", "Mixture composition"),
    ("wt%",       "weight percent",                   "concentration", "Solids / slurry composition"),

    # --- Viscosity -----------------------------------------------------------
    ("cP",        "centipoise",                       "viscosity",   "Dynamic viscosity, water ≈ 1 cP at 20 °C"),
    ("cSt",       "centistokes",                      "viscosity",   "Kinematic viscosity"),
    ("Pa·s",      "pascal-seconds",                   "viscosity",   "SI dynamic viscosity"),
    ("mPa·s",     "millipascal-seconds",              "viscosity",   "Equivalent to cP"),

    # --- Water quality -------------------------------------------------------
    ("pH",        "pH",                               "ph",          "Acidity / alkalinity, 0–14"),
    ("mS/cm",     "millisiemens per centimeter",      "conductivity", "Conductivity, common in water"),
    ("μS/cm",     "microsiemens per centimeter",      "conductivity", "Low conductivity (pure water)"),
    ("NTU",       "nephelometric turbidity units",    "turbidity",   "Standard turbidity measurement"),
    ("FTU",       "formazin turbidity units",         "turbidity",   "Equivalent to NTU"),
    ("FNU",       "formazin nephelometric units",     "turbidity",   "ISO standard turbidity"),

    # --- Gas / Oil specific --------------------------------------------------
    ("SG",        "specific gravity",                 "dimensionless", "Density relative to water/air"),
    ("°API",      "API gravity",                      "density",     "Oil density convention"),
    ("BTU/scf",   "BTU per standard cubic foot",      "heating_value", "Gas calorific value, US"),
    ("kJ/m³",     "kilojoules per cubic meter",       "heating_value", None),
    ("MJ/Sm³",    "megajoules per std cubic meter",   "heating_value", "Gas calorific value, metric"),
    ("kJ/kg",     "kilojoules per kilogram",          "heating_value", "Specific energy / enthalpy"),
    ("mol_frac",  "mole fraction",                    "dimensionless", "Composition fraction, 0–1"),

    # --- Humidity ------------------------------------------------------------
    ("%RH",       "relative humidity percent",        "humidity",    "Common HVAC and pharma metric"),
    ("g/kg",      "grams per kilogram",               "humidity",    "Absolute humidity"),
    ("°C dp",     "dew point in Celsius",             "humidity",    "Air moisture saturation point"),

    # --- HVAC / facilities ---------------------------------------------------
    ("CFM",       "cubic feet per minute",            "flow_volume", "HVAC air flow, US convention"),
    ("CMH",       "cubic meters per hour",            "flow_volume", "Alias of m³/h, HVAC convention"),
    ("lux",       "lux",                              "illuminance", "Light intensity, pharma cleanrooms"),

    # --- Dimensionless / process placeholders --------------------------------
    ("",          "(no unit)",                        "dimensionless", "For tags that have no physical unit"),
    ("count",     "count",                            "dimensionless", "Counters, sequence numbers"),
    ("%open",     "percent open",                     "ratio",       "Valve / damper position"),
    ("%load",     "percent load",                     "ratio",       "Motor or vessel loading"),
]


def upgrade() -> None:
    # 1. Create the master table
    op.create_table(
        "engineering_units",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("quantity_kind", sa.String(32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
    )

    # Index for grouped dropdown filtering
    op.create_index(
        "ix_engineering_units_kind_enabled",
        "engineering_units",
        ["quantity_kind", "enabled"],
    )

    # 2. Seed the master with the comprehensive industry list
    op.bulk_insert(
        sa.table(
            "engineering_units",
            sa.column("code", sa.String),
            sa.column("label", sa.String),
            sa.column("quantity_kind", sa.String),
            sa.column("description", sa.Text),
            sa.column("is_system", sa.Boolean),
        ),
        [
            {
                "code": code,
                "label": label,
                "quantity_kind": kind,
                "description": desc,
                "is_system": True,
            }
            for (code, label, kind, desc) in SEED_UNITS
        ],
    )

    # 3. Extend tags table — FK column, keep existing text column as override
    op.add_column(
        "tags",
        sa.Column("engineering_unit_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tags_engineering_unit",
        "tags",
        "engineering_units",
        ["engineering_unit_id"],
        ["id"],
        ondelete="SET NULL",   # if a unit is deleted, tag becomes unitless
    )

    # 4. Constraint: at most one of (FK, custom) is set
    op.create_check_constraint(
        "ck_tags_engineering_unit_exclusive",
        "tags",
        "NOT (engineering_unit_id IS NOT NULL AND engineering_unit IS NOT NULL "
        "AND engineering_unit <> '')",
    )

    # 5. Non-destructive backfill: match existing free-text values against the
    #    seed master case-insensitively (on code first, then label). Matches
    #    move to FK and clear the text. Non-matches stay as text overrides.
    op.execute("""
        UPDATE tags AS t
        SET engineering_unit_id = eu.id,
            engineering_unit    = NULL
        FROM engineering_units AS eu
        WHERE t.engineering_unit IS NOT NULL
          AND t.engineering_unit <> ''
          AND (
              LOWER(t.engineering_unit) = LOWER(eu.code)
              OR LOWER(t.engineering_unit) = LOWER(eu.label)
          );
    """)

    # 6. Index on the new FK for join performance in the live view
    op.create_index("ix_tags_engineering_unit_id", "tags", ["engineering_unit_id"])


def downgrade() -> None:
    # Reverse order — undo backfill first (best-effort restore of text), then
    # drop the constraint, FK, column, and master table.
    op.execute("""
        UPDATE tags AS t
        SET engineering_unit = eu.code,
            engineering_unit_id = NULL
        FROM engineering_units AS eu
        WHERE t.engineering_unit_id = eu.id;
    """)
    op.drop_index("ix_tags_engineering_unit_id", table_name="tags")
    op.drop_constraint("ck_tags_engineering_unit_exclusive", "tags", type_="check")
    op.drop_constraint("fk_tags_engineering_unit", "tags", type_="foreignkey")
    op.drop_column("tags", "engineering_unit_id")
    op.drop_index("ix_engineering_units_kind_enabled", table_name="engineering_units")
    op.drop_table("engineering_units")

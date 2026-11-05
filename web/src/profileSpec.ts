// Опис анкети об'єкта для форми (FR-PRP-8). Значення — як у domain/property_profile.py.
// Підписи: pf_<поле>, pf_<поле>__<значення>, pf_section_<розділ> у locales/uk.json.
// Анкета не фінальна: нове поле = рядок тут + поле в PropertyDetails на бекенді.

export type FieldSpec =
  | { key: string; kind: "choice"; options: string[] }
  | { key: string; kind: "multi"; options: string[] }
  | { key: string; kind: "bool" }
  | { key: string; kind: "int"; min: number; max: number }
  | { key: string; kind: "decimal"; min: number; max: number }
  | { key: string; kind: "text"; max: number };

export type SectionSpec = { key: string; fields: FieldSpec[] };

export const PROFILE_SECTIONS: SectionSpec[] = [
  {
    key: "utilities",
    fields: [
      { key: "heating", kind: "choice", options: ["central", "building_boiler", "individual_gas", "individual_electric", "other"] },
      { key: "utilities_winter_uah", kind: "int", min: 0, max: 100000 },
      { key: "utilities_summer_uah", kind: "int", min: 0, max: 100000 },
      { key: "meters", kind: "multi", options: ["electricity", "water", "gas", "heat"] },
      { key: "hot_water", kind: "choice", options: ["central", "heating_boiler", "electric_boiler", "gas_heater"] },
      { key: "stove", kind: "choice", options: ["gas", "electric", "induction"] },
      { key: "internet", kind: "choice", options: ["none", "cable", "fiber", "mobile"] },
    ],
  },
  {
    key: "blackout",
    fields: [
      { key: "apartment_backup_power", kind: "choice", options: ["none", "power_station", "inverter", "generator"] },
      { key: "blackout_ready", kind: "multi", options: ["elevator", "water", "heating", "internet", "lighting"] },
    ],
  },
  {
    key: "building",
    fields: [
      { key: "shelter", kind: "choice", options: ["none", "basement", "underground_parking", "dedicated", "nearby"] },
      {
        key: "building_type",
        kind: "choice",
        options: ["austrian_polish", "stalinka", "khrushchovka", "czech", "soviet_panel", "gostynka", "new_build", "private_house", "other"],
      },
      { key: "wall_material", kind: "choice", options: ["brick", "panel", "monolith_frame", "block", "wood", "other"] },
      { key: "year_built", kind: "int", min: 1700, max: 2035 },
      { key: "floors_total", kind: "int", min: 1, max: 60 },
      { key: "has_elevator", kind: "bool" },
      { key: "parking", kind: "choice", options: ["none", "street", "yard", "underground", "garage"] },
      {
        key: "building_features",
        kind: "multi",
        options: ["closed_territory", "video_surveillance", "concierge", "playground", "bicycle_storage", "intercom"],
      },
    ],
  },
  {
    key: "apartment",
    fields: [
      { key: "repair", kind: "choice", options: ["designer", "euro", "cosmetic", "livable", "needs_repair"] },
      { key: "layout", kind: "choice", options: ["separate", "adjacent", "mixed", "studio", "kitchen_studio", "multilevel"] },
      { key: "bathroom", kind: "choice", options: ["separate", "combined", "several"] },
      { key: "area_living", kind: "decimal", min: 1, max: 1000 },
      { key: "area_kitchen", kind: "decimal", min: 1, max: 200 },
      { key: "ceiling_height", kind: "decimal", min: 2, max: 8 },
      { key: "balconies", kind: "int", min: 0, max: 6 },
      { key: "windows_view", kind: "choice", options: ["yard", "street", "both"] },
      { key: "first_rental", kind: "bool" },
    ],
  },
  {
    key: "equipment",
    fields: [
      { key: "furnishing", kind: "choice", options: ["none", "partial", "full"] },
      {
        key: "appliances",
        kind: "multi",
        options: ["fridge", "washing_machine", "dryer", "dishwasher", "oven", "microwave", "hob", "hood", "kettle", "coffee_machine", "tv", "vacuum", "iron"],
      },
      {
        key: "comfort",
        kind: "multi",
        options: ["air_conditioner", "floor_heating", "water_filter", "bathtub", "shower", "wardrobe_room", "storage_room", "workspace", "security_door", "alarm"],
      },
      { key: "accessibility", kind: "multi", options: ["step_free_entrance", "wide_doors", "step_free_shower", "grab_bars", "step_free_shelter"] },
    ],
  },
  {
    key: "rules",
    fields: [
      { key: "pets", kind: "choice", options: ["no", "cats", "small_dogs", "any", "discuss"] },
      { key: "children", kind: "choice", options: ["yes", "no", "discuss"] },
      { key: "smoking", kind: "choice", options: ["no", "balcony", "yes"] },
      { key: "max_occupants", kind: "int", min: 1, max: 20 },
      { key: "residence_registration", kind: "bool" },
    ],
  },
  {
    key: "about",
    fields: [{ key: "description", kind: "text", max: 3000 }],
  },
];

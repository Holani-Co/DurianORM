<script setup>
// Store editor. Lists the registry's stores (search + vertical filter); each row
// expands to edit the details a customer receives — display name, address,
// manager, phone, email, timing, map link, holiday, parking and the other Master
// list details — or disable the store. Deal routing (the CRM owner) is its own
// block with a Zoho user picker. An empty field means "use the registry value";
// only changed fields are saved, so the override stays minimal. A per-vertical
// serviceable radius is edited too. Saves publish to the bridge's stores
// override (validate -> publish).
import { ref, reactive, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  effective: { type: Array, default: () => [] },
  override: { type: Object, default: () => ({}) },
  verticals: { type: Array, default: () => [] },
  editableFields: { type: Array, default: () => [] },
  radiusDefaults: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['published']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

// Display fields shown in the form (coords are intentionally left out of the
// UI; the CRM owner has its own "Deal routing" block below).
const FORM_FIELDS = [
  'card_name',
  'address',
  'city',
  'pincode',
  'manager',
  'phone',
  'email',
  'timing',
  'map_url',
  'holiday',
  'parking',
  'store_type',
  'area',
  'floors',
  'escalator',
].filter(f => props.editableFields.includes(f));
const canEditOwner = props.editableFields.includes('crm_owner_id');

// Active Zoho CRM users for the deal-owner picker. If the list can't load (e.g.
// the CRM token lacks the users scope) we fall back to the raw owner-ID field.
const owners = ref([]);
const ownersFailed = ref(false);

onMounted(async () => {
  if (!canEditOwner) return;
  try {
    const { data } = await axios.get(
      `/api/v1/accounts/${accountId.value}/integrations/stores_config/zoho_owners`
    );
    owners.value = data.owners || [];
    ownersFailed.value = Boolean(data.error);
  } catch (e) {
    ownersFailed.value = true;
  }
});

// A store whose owner is already overridden: the empty option removes that
// override, so the store falls back to its registry owner.
const ownerOverridden = id =>
  Boolean(props.override.stores?.[id]?.crm_owner_id);

const ownerLabel = id => {
  const o = owners.value.find(u => u.id === id);
  if (!o) return id || '';
  return o.email ? `${o.name || o.email} (${o.email})` : o.name;
};

const search = ref('');
const verticalFilter = ref('all');
const expandedId = ref(null);
const busy = ref(false);
const errors = ref([]);

// Working copy of the override, seeded from what's live.
const storeEdits = reactive({ ...(props.override.stores || {}) });
const radius = reactive(
  Object.fromEntries(
    props.verticals.map(v => [
      v,
      (props.override.radius_km || {})[v] ?? props.radiusDefaults[v] ?? '',
    ])
  )
);

// "Upcoming stores": locations with no store yet but one opening soon. When a
// customer mentions one, the bot says it's coming soon + captures them.
const upcomingList = ref(
  (props.override.upcoming || []).map(e => ({
    vertical: e.vertical || props.verticals[0] || 'furniture',
    area: e.area || '',
    pincodes: (e.pincodes || []).join(', '),
    note: e.note || '',
    enabled: e.enabled !== false,
  }))
);
const addUpcoming = () => {
  upcomingList.value.push({
    vertical: props.verticals[0] || 'furniture',
    area: '',
    pincodes: '',
    note: '',
    enabled: true,
  });
};
const removeUpcoming = i => upcomingList.value.splice(i, 1);

// Stores added here (an upcoming store that has opened). Kept as they are on
// every save; they show up in the store list below like registry stores.
const newStores = ref([...(props.override.new_stores || [])]);
const VERTICAL_LABEL = { furniture: 'Furniture', doors: 'Doors', fhc: 'FHC' };
const NEW_STORE_REQUIRED = ['card_name', 'address', 'city', 'pincode'];

// "Store is open – add details" on an upcoming row: the store form, pre-filled
// from the row. "Make it live" turns it into a store and saves straight away.
const goLive = ref(null); // { index, draft }
const goLiveErrors = ref([]);
const startGoLive = i => {
  const u = upcomingList.value[i];
  const area = (u.area || '').trim();
  const draft = Object.fromEntries(FORM_FIELDS.map(f => [f, '']));
  draft.card_name = area
    ? `${VERTICAL_LABEL[u.vertical] || u.vertical} - ${area}`
    : '';
  draft.city = area;
  draft.pincode = (u.pincodes || '').split(',')[0].trim();
  draft.crm_owner_id = '';
  draft.coords = '';
  goLive.value = { index: i, vertical: u.vertical, draft };
  goLiveErrors.value = [];
};
const slug = text =>
  (text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
const uniqueId = base => {
  const taken = new Set([
    ...props.effective.map(s => s.id),
    ...newStores.value.map(s => s.id),
  ]);
  let id = base;
  let n = 2;
  while (taken.has(id)) {
    id = `${base}-${n}`;
    n += 1;
  }
  return id;
};

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  return props.effective.filter(s => {
    if (
      verticalFilter.value !== 'all' &&
      !(s.verticals || []).includes(verticalFilter.value)
    ) {
      return false;
    }
    if (!q) return true;
    return (
      (s.card_name || '').toLowerCase().includes(q) ||
      (s.name || '').toLowerCase().includes(q) ||
      (s.city || '').toLowerCase().includes(q)
    );
  });
});

const editsFor = id => {
  if (!storeEdits[id]) storeEdits[id] = {};
  return storeEdits[id];
};

const isDisabled = id => Boolean(storeEdits[id]?.disabled);

const toggleDisabled = id => {
  editsFor(id).disabled = !isDisabled(id);
};

const buildDoc = () => {
  const stores = {};
  Object.entries(storeEdits).forEach(([id, edit]) => {
    const clean = {};
    Object.entries(edit).forEach(([k, v]) => {
      if (k === 'disabled') {
        if (v) clean.disabled = true;
      } else if (typeof v === 'string' && v.trim() !== '') {
        clean[k] = v.trim();
      }
    });
    if (Object.keys(clean).length) stores[id] = clean;
  });
  const radiusKm = {};
  Object.entries(radius).forEach(([v, km]) => {
    const n = Number(km);
    if (km !== '' && !Number.isNaN(n) && n > 0) radiusKm[v] = n;
  });
  const upcoming = upcomingList.value
    .map(e => {
      const area = (e.area || '').trim();
      const pincodes = (e.pincodes || '')
        .split(',')
        .map(p => p.trim())
        .filter(Boolean);
      if (!area && !pincodes.length) return null;
      const entry = { vertical: e.vertical, enabled: e.enabled !== false };
      if (area) entry.area = area;
      if (pincodes.length) entry.pincodes = pincodes;
      if ((e.note || '').trim()) entry.note = e.note.trim();
      return entry;
    })
    .filter(Boolean);
  const doc = {};
  if (Object.keys(stores).length) doc.stores = stores;
  if (Object.keys(radiusKm).length) doc.radius_km = radiusKm;
  if (upcoming.length) doc.upcoming = upcoming;
  if (newStores.value.length) doc.new_stores = newStores.value;
  return doc;
};

async function save() {
  if (busy.value) return;
  busy.value = true;
  errors.value = [];
  const base = `/api/v1/accounts/${accountId.value}/integrations/stores_config`;
  const doc = buildDoc();
  try {
    const { data: v } = await axios.post(`${base}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || [t('STORES_CONFIG.SAVE_FAILED')];
      return;
    }
    await axios.post(`${base}/publish`, {
      doc,
      note: t('STORES_CONFIG.PUBLISH_NOTE'),
    });
    useAlert(t('STORES_CONFIG.SAVED'));
    emit('published');
  } catch (e) {
    useAlert(e?.response?.data?.error || t('STORES_CONFIG.SAVE_FAILED'));
  } finally {
    busy.value = false;
  }
}

async function confirmGoLive() {
  const { index, vertical, draft } = goLive.value;
  const missing = NEW_STORE_REQUIRED.filter(f => !(draft[f] || '').trim());
  if (missing.length) {
    goLiveErrors.value = [
      t('STORES_CONFIG.UPCOMING.REQUIRED', {
        fields: missing.map(f => t(`STORES_CONFIG.FIELDS.${f}`)).join(', '),
      }),
    ];
    return;
  }
  const store = { vertical };
  Object.entries(draft).forEach(([k, v]) => {
    if (k !== 'coords' && typeof v === 'string' && v.trim())
      store[k] = v.trim();
  });
  const coords = (draft.coords || '').trim();
  if (coords) {
    const [lat, lon] = coords.split(',').map(x => Number(x.trim()));
    if (
      coords.split(',').length !== 2 ||
      Number.isNaN(lat) ||
      Number.isNaN(lon) ||
      Math.abs(lat) > 90 ||
      Math.abs(lon) > 180
    ) {
      goLiveErrors.value = [t('STORES_CONFIG.UPCOMING.COORDS_INVALID')];
      return;
    }
    store.lat = lat;
    store.lon = lon;
  }
  store.id = uniqueId(`${vertical}-${slug(store.card_name)}`);
  newStores.value.push(store);
  upcomingList.value.splice(index, 1);
  goLive.value = null;
  await save();
}
</script>

<template>
  <div class="max-w-3xl">
    <!-- per-vertical radius -->
    <div class="p-4 mb-5 border rounded-xl border-n-weak">
      <div class="mb-1 text-sm font-medium text-n-slate-12">
        {{ t('STORES_CONFIG.RADIUS.TITLE') }}
      </div>
      <p class="mb-3 text-xs text-n-slate-11">
        {{ t('STORES_CONFIG.RADIUS.HINT') }}
      </p>
      <div class="flex flex-wrap gap-4">
        <label
          v-for="v in verticals"
          :key="v"
          class="flex items-center gap-2 text-sm text-n-slate-11"
        >
          <span class="capitalize">{{ v }}</span>
          <input
            v-model="radius[v]"
            type="number"
            min="1"
            class="w-24 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <span class="text-xs text-n-slate-10">km</span>
        </label>
      </div>
    </div>

    <!-- upcoming stores -->
    <div class="p-4 mb-5 border rounded-xl border-n-weak">
      <div class="flex items-center justify-between mb-1">
        <div class="text-sm font-medium text-n-slate-12">
          {{ t('STORES_CONFIG.UPCOMING.TITLE') }}
        </div>
        <button
          type="button"
          class="px-2 py-1 text-xs font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
          @click="addUpcoming"
        >
          {{ t('STORES_CONFIG.UPCOMING.ADD') }}
        </button>
      </div>
      <p class="mb-3 text-xs text-n-slate-11">
        {{ t('STORES_CONFIG.UPCOMING.HINT') }}
      </p>
      <p v-if="!upcomingList.length" class="text-xs text-n-slate-10">
        {{ t('STORES_CONFIG.UPCOMING.EMPTY') }}
      </p>
      <div
        v-for="(u, i) in upcomingList"
        :key="i"
        class="py-2 border-t border-n-weak"
      >
        <div class="grid grid-cols-1 gap-2 sm:grid-cols-5">
          <select
            v-model="u.vertical"
            class="px-2 py-1 text-sm capitalize border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          >
            <option v-for="v in verticals" :key="v" :value="v">{{ v }}</option>
          </select>
          <input
            v-model="u.area"
            type="text"
            :placeholder="t('STORES_CONFIG.UPCOMING.AREA_PH')"
            class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <input
            v-model="u.pincodes"
            type="text"
            :placeholder="t('STORES_CONFIG.UPCOMING.PINCODES_PH')"
            class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <input
            v-model="u.note"
            type="text"
            :placeholder="t('STORES_CONFIG.UPCOMING.NOTE_PH')"
            class="px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <div class="flex items-center justify-between gap-2">
            <label class="flex items-center gap-1 text-xs text-n-slate-11">
              <input v-model="u.enabled" type="checkbox" />
              {{ t('STORES_CONFIG.UPCOMING.ENABLED') }}
            </label>
            <button
              type="button"
              class="px-2 py-1 text-xs rounded-lg text-n-ruby-11 hover:opacity-80"
              @click="removeUpcoming(i)"
            >
              {{ t('STORES_CONFIG.UPCOMING.REMOVE') }}
            </button>
          </div>
        </div>
        <button
          v-if="!goLive || goLive.index !== i"
          type="button"
          class="px-2 py-1 mt-2 text-xs font-medium border rounded-lg border-n-teal-7 text-n-teal-11 hover:bg-n-alpha-1"
          @click="startGoLive(i)"
        >
          {{ t('STORES_CONFIG.UPCOMING.GO_LIVE') }}
        </button>
        <div
          v-else
          class="p-3 mt-2 border rounded-lg border-n-teal-7 bg-n-alpha-1"
        >
          <div class="text-sm font-medium text-n-slate-12">
            {{ t('STORES_CONFIG.UPCOMING.GO_LIVE_TITLE') }}
          </div>
          <p class="mt-1 text-xs text-n-slate-11">
            {{ t('STORES_CONFIG.UPCOMING.GO_LIVE_HINT') }}
          </p>
          <div class="grid grid-cols-1 gap-3 mt-3 sm:grid-cols-2">
            <label
              v-for="f in FORM_FIELDS"
              :key="f"
              class="text-xs text-n-slate-11"
            >
              {{ t(`STORES_CONFIG.FIELDS.${f}`) }}
              <input
                v-model="goLive.draft[f]"
                type="text"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
            </label>
            <label class="text-xs text-n-slate-11 sm:col-span-2">
              {{ t('STORES_CONFIG.UPCOMING.COORDS') }}
              <input
                v-model="goLive.draft.coords"
                type="text"
                :placeholder="t('STORES_CONFIG.UPCOMING.COORDS_PH')"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
              <span class="block mt-1 text-n-slate-10">
                {{ t('STORES_CONFIG.UPCOMING.COORDS_HINT') }}
              </span>
            </label>
            <label
              v-if="canEditOwner"
              class="text-xs text-n-slate-11 sm:col-span-2"
            >
              {{ t('STORES_CONFIG.DEAL_ROUTING.TITLE') }}
              <select
                v-if="owners.length"
                v-model="goLive.draft.crm_owner_id"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              >
                <option value="">
                  {{ t('STORES_CONFIG.DEAL_ROUTING.NONE') }}
                </option>
                <option v-for="o in owners" :key="o.id" :value="o.id">
                  {{ ownerLabel(o.id) }}
                </option>
              </select>
              <input
                v-else
                v-model="goLive.draft.crm_owner_id"
                type="text"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
            </label>
          </div>
          <ul
            v-if="goLiveErrors.length"
            class="mt-3 text-xs list-disc list-inside text-n-ruby-11"
          >
            <li v-for="(e, k) in goLiveErrors" :key="k">{{ e }}</li>
          </ul>
          <div class="flex justify-end gap-2 mt-3">
            <button
              type="button"
              class="px-3 py-1 text-sm border rounded-lg border-n-weak text-n-slate-11"
              @click="goLive = null"
            >
              {{ t('STORES_CONFIG.UPCOMING.GO_LIVE_CANCEL') }}
            </button>
            <button
              type="button"
              class="px-3 py-1 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
              :disabled="busy"
              @click="confirmGoLive"
            >
              {{ t('STORES_CONFIG.UPCOMING.GO_LIVE_SAVE') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- filters -->
    <div class="flex flex-wrap items-center gap-2 mb-3">
      <input
        v-model="search"
        type="text"
        :placeholder="t('STORES_CONFIG.SEARCH_PLACEHOLDER')"
        class="flex-1 min-w-[12rem] px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
      />
      <button
        class="px-2 py-1 text-xs border rounded-lg"
        :class="
          verticalFilter === 'all'
            ? 'border-n-brand text-n-slate-12'
            : 'border-n-weak text-n-slate-11'
        "
        @click="verticalFilter = 'all'"
      >
        {{ t('STORES_CONFIG.ALL') }}
      </button>
      <button
        v-for="v in verticals"
        :key="v"
        class="px-2 py-1 text-xs capitalize border rounded-lg"
        :class="
          verticalFilter === v
            ? 'border-n-brand text-n-slate-12'
            : 'border-n-weak text-n-slate-11'
        "
        @click="verticalFilter = v"
      >
        {{ v }}
      </button>
    </div>

    <div
      v-if="errors.length"
      class="p-3 mb-3 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <ul class="list-disc list-inside">
        <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
      </ul>
    </div>

    <p v-if="!filtered.length" class="py-8 text-sm text-n-slate-11">
      {{ t('STORES_CONFIG.EMPTY') }}
    </p>

    <div v-else class="flex flex-col gap-2">
      <div
        v-for="s in filtered"
        :key="s.id"
        class="border rounded-xl border-n-weak"
        :class="isDisabled(s.id) ? 'opacity-60' : ''"
      >
        <button
          type="button"
          class="flex flex-wrap items-center w-full gap-2 px-4 py-3 text-left"
          @click="expandedId = expandedId === s.id ? null : s.id"
        >
          <span class="font-medium text-n-slate-12">{{
            s.card_name || s.name
          }}</span>
          <span class="text-xs text-n-slate-10">{{ s.city }}</span>
          <span
            v-for="v in s.verticals"
            :key="v"
            class="px-2 py-0.5 text-xs capitalize rounded-full bg-n-alpha-2 text-n-slate-11"
          >
            {{ v }}
          </span>
          <span
            v-if="s.added_in_settings"
            class="px-2 py-0.5 text-xs rounded-full bg-n-teal-3 text-n-teal-11"
          >
            {{ t('STORES_CONFIG.ADDED_BADGE') }}
          </span>
          <span
            v-if="isDisabled(s.id)"
            class="px-2 py-0.5 text-xs rounded-full bg-n-amber-3 text-n-amber-11"
          >
            {{ t('STORES_CONFIG.DISABLED') }}
          </span>
          <span class="ml-auto text-xs text-n-slate-10">
            {{
              expandedId === s.id
                ? t('STORES_CONFIG.HIDE')
                : t('STORES_CONFIG.EDIT')
            }}
          </span>
        </button>

        <div
          v-if="expandedId === s.id"
          class="px-4 pb-4 border-t border-n-weak"
        >
          <div class="grid grid-cols-1 gap-3 mt-3 sm:grid-cols-2">
            <label
              v-for="f in FORM_FIELDS"
              :key="f"
              class="text-xs text-n-slate-11"
            >
              {{ t(`STORES_CONFIG.FIELDS.${f}`) }}
              <input
                v-model="editsFor(s.id)[f]"
                type="text"
                :placeholder="s[f] || ''"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
            </label>
          </div>

          <div v-if="canEditOwner" class="pt-3 mt-4 border-t border-n-weak">
            <div class="mb-1 text-xs font-medium text-n-slate-12">
              {{ t('STORES_CONFIG.DEAL_ROUTING.TITLE') }}
            </div>
            <p class="mb-2 text-xs text-n-slate-10">
              {{ t('STORES_CONFIG.DEAL_ROUTING.HINT') }}
            </p>
            <select
              v-if="owners.length"
              :value="editsFor(s.id).crm_owner_id || ''"
              class="w-full px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              @change="editsFor(s.id).crm_owner_id = $event.target.value"
            >
              <option value="">
                <template v-if="ownerOverridden(s.id)">
                  {{ t('STORES_CONFIG.DEAL_ROUTING.DEFAULT') }}
                </template>
                <template v-else-if="s.crm_owner_id">
                  {{
                    t('STORES_CONFIG.DEAL_ROUTING.KEEP', {
                      owner: ownerLabel(s.crm_owner_id),
                    })
                  }}
                </template>
                <template v-else>
                  {{ t('STORES_CONFIG.DEAL_ROUTING.NONE') }}
                </template>
              </option>
              <option v-for="o in owners" :key="o.id" :value="o.id">
                {{ ownerLabel(o.id) }}
              </option>
            </select>
            <template v-else>
              <input
                v-model="editsFor(s.id).crm_owner_id"
                type="text"
                :placeholder="s.crm_owner_id || ''"
                class="w-full px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
              <p v-if="ownersFailed" class="mt-1 text-xs text-n-amber-11">
                {{ t('STORES_CONFIG.DEAL_ROUTING.UNAVAILABLE') }}
              </p>
            </template>
          </div>

          <label class="flex items-center gap-2 mt-4 text-sm text-n-slate-11">
            <input
              type="checkbox"
              :checked="isDisabled(s.id)"
              @change="toggleDisabled(s.id)"
            />
            {{ t('STORES_CONFIG.DISABLE_LABEL') }}
          </label>
          <p class="mt-2 text-xs text-n-slate-10">
            {{ t('STORES_CONFIG.FIELD_HINT') }}
          </p>
        </div>
      </div>
    </div>

    <div class="sticky bottom-0 flex justify-end py-3 mt-4 bg-n-background">
      <button
        type="button"
        class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
        :disabled="busy"
        @click="save"
      >
        {{ busy ? t('STORES_CONFIG.SAVING') : t('STORES_CONFIG.SAVE') }}
      </button>
    </div>
  </div>
</template>

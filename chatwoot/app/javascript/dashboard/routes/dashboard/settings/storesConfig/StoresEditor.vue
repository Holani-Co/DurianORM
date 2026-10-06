<script setup>
// Store editor. Lists the registry's stores (search + vertical filter); each row
// expands to edit the details a customer receives — display name, address,
// manager, phone, email, timing, map link, CRM owner — or disable the store. An
// empty field means "use the registry value"; only changed fields are saved, so
// the override stays minimal. A per-vertical serviceable radius is edited too.
// Saves publish to the bridge's stores override (validate -> publish).
import { ref, reactive, computed } from 'vue';
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

// Fields shown in the form (coords are intentionally left out of the UI).
const FORM_FIELDS = [
  'card_name', 'address', 'city', 'pincode', 'manager',
  'phone', 'email', 'timing', 'map_url', 'crm_owner_id',
].filter(f => props.editableFields.includes(f));

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

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  return props.effective.filter(s => {
    if (verticalFilter.value !== 'all' && !(s.verticals || []).includes(verticalFilter.value)) {
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
  const doc = {};
  if (Object.keys(stores).length) doc.stores = stores;
  if (Object.keys(radiusKm).length) doc.radius_km = radiusKm;
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
        :class="verticalFilter === 'all' ? 'border-n-brand text-n-slate-12' : 'border-n-weak text-n-slate-11'"
        @click="verticalFilter = 'all'"
      >
        {{ t('STORES_CONFIG.ALL') }}
      </button>
      <button
        v-for="v in verticals"
        :key="v"
        class="px-2 py-1 text-xs capitalize border rounded-lg"
        :class="verticalFilter === v ? 'border-n-brand text-n-slate-12' : 'border-n-weak text-n-slate-11'"
        @click="verticalFilter = v"
      >
        {{ v }}
      </button>
    </div>

    <div v-if="errors.length" class="p-3 mb-3 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11">
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
          <span class="font-medium text-n-slate-12">{{ s.card_name || s.name }}</span>
          <span class="text-xs text-n-slate-10">{{ s.city }}</span>
          <span
            v-for="v in s.verticals"
            :key="v"
            class="px-2 py-0.5 text-xs capitalize rounded-full bg-n-alpha-2 text-n-slate-11"
          >{{ v }}</span>
          <span
            v-if="isDisabled(s.id)"
            class="px-2 py-0.5 text-xs rounded-full bg-n-amber-3 text-n-amber-11"
          >{{ t('STORES_CONFIG.DISABLED') }}</span>
          <span class="ml-auto text-xs text-n-slate-10">
            {{ expandedId === s.id ? t('STORES_CONFIG.HIDE') : t('STORES_CONFIG.EDIT') }}
          </span>
        </button>

        <div v-if="expandedId === s.id" class="px-4 pb-4 border-t border-n-weak">
          <div class="grid grid-cols-1 gap-3 mt-3 sm:grid-cols-2">
            <label v-for="f in FORM_FIELDS" :key="f" class="text-xs text-n-slate-11">
              {{ t(`STORES_CONFIG.FIELDS.${f}`) }}
              <input
                v-model="editsFor(s.id)[f]"
                type="text"
                :placeholder="s[f] || ''"
                class="w-full px-2 py-1 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
              />
            </label>
          </div>
          <label class="flex items-center gap-2 mt-4 text-sm text-n-slate-11">
            <input type="checkbox" :checked="isDisabled(s.id)" @change="toggleDisabled(s.id)" />
            {{ t('STORES_CONFIG.DISABLE_LABEL') }}
          </label>
          <p class="mt-2 text-xs text-n-slate-10">{{ t('STORES_CONFIG.FIELD_HINT') }}</p>
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

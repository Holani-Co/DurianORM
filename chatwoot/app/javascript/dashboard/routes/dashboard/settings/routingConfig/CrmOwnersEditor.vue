<script setup>
// Phase 2 — editable CRM owners. Reassign a territory to a different owner by
// picking from a dropdown of known owners (email + Zoho ID always travel together,
// so a reassignment can never create a mismatched pair). New owners can be added
// to the picker with a validated email + ID. Changes are collected as a draft and
// published to the bridge in one go (validate -> publish -> live).
//
// Owner maps come in three shapes:
//   • map  — { territory: {owner_email, owner_id, ...} }   (homestudio, private, doors)
//   • flat — { owner_email, owner_id, ... }                (franchise, fallback)
//   • list — { territory: [ {owner_email, owner_id}, ... ] } round-robin (govt_bulk)
// All three are editable: single-owner shapes via one dropdown; the round-robin
// rotation as a list of dropdowns with add/remove (a territory keeps ≥1 owner).
import { ref, reactive, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  effective: { type: Object, default: () => ({}) },
  override: { type: Object, default: () => ({}) },
  knownOwners: { type: Array, default: () => [] },
});
const emit = defineEmits(['published']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const OWNER_MAPS = [
  { key: 'crm_owner_routing_homestudio', kind: 'map' },
  { key: 'crm_owner_routing_private', kind: 'map' },
  { key: 'crm_owner_routing_doors', kind: 'map' },
  { key: 'crm_owner_routing_govt_bulk', kind: 'list' },
  { key: 'crm_owner_routing_franchise', kind: 'flat' },
  { key: 'crm_owner_routing_fallback', kind: 'flat' },
];

const extraOwners = reactive([]); // owners added to the picker this session
const edits = reactive([]); // { mapKey, territory (null for flat), value }
const busy = ref(false);
const errors = ref([]);
const newEmail = ref('');
const newId = ref('');

const maps = computed(() =>
  OWNER_MAPS.filter(m => props.effective[m.key] != null).map(m => ({
    ...m,
    label: t(`ROUTING_CONFIG.OWNERS.MAPS.${m.key}`),
  }))
);

const allOwners = computed(() => {
  const seen = new Set();
  const out = [];
  [...props.knownOwners, ...extraOwners].forEach(o => {
    const id = `${o.owner_email}|${o.owner_id}`;
    if (o.owner_email && !seen.has(id)) {
      seen.add(id);
      out.push(o);
    }
  });
  return out.sort((a, b) => a.owner_email.localeCompare(b.owner_email));
});

const validEmail = e => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((e || '').trim());
const dirtyCount = computed(() => edits.length);

const territories = mapKey => Object.keys(props.effective[mapKey] || {}).sort();

function findEdit(mapKey, territory) {
  return edits.find(e => e.mapKey === mapKey && e.territory === territory);
}

// Current owner object for a slot: the pending edit if any, else the live value.
function currentOwner(mapKey, territory) {
  const e = findEdit(mapKey, territory);
  if (e) return e.value;
  return territory === null
    ? props.effective[mapKey]
    : (props.effective[mapKey] || {})[territory];
}

function currentEmail(mapKey, territory) {
  return (currentOwner(mapKey, territory) || {}).owner_email || '';
}

// Reassign — preserves any extra fields (e.g. business_vertical) from the original.
function reassign(mapKey, territory, email) {
  const owner = allOwners.value.find(o => o.owner_email === email);
  if (!owner) return;
  const orig =
    territory === null
      ? props.effective[mapKey] || {}
      : (props.effective[mapKey] || {})[territory] || {};
  const value = {
    ...orig,
    owner_email: owner.owner_email,
    owner_id: owner.owner_id,
  };
  const existing = findEdit(mapKey, territory);
  if (existing) existing.value = value;
  else edits.push({ mapKey, territory, value });
}

// --- round-robin (list) editing ---
function currentList(mapKey, territory) {
  const e = findEdit(mapKey, territory);
  const list = e ? e.value : (props.effective[mapKey] || {})[territory] || [];
  return Array.isArray(list) ? list : [];
}

function commitList(mapKey, territory, list) {
  const existing = findEdit(mapKey, territory);
  if (existing) existing.value = list;
  else edits.push({ mapKey, territory, value: list });
}

function setListOwner(mapKey, territory, index, email) {
  const owner = allOwners.value.find(o => o.owner_email === email);
  if (!owner) return;
  const list = currentList(mapKey, territory).map(o => ({ ...o }));
  list[index] = { owner_email: owner.owner_email, owner_id: owner.owner_id };
  commitList(mapKey, territory, list);
}

function addListOwner(mapKey, territory) {
  const owner = allOwners.value[0];
  if (!owner) return;
  const list = currentList(mapKey, territory).map(o => ({ ...o }));
  list.push({ owner_email: owner.owner_email, owner_id: owner.owner_id });
  commitList(mapKey, territory, list);
}

function removeListOwner(mapKey, territory, index) {
  const list = currentList(mapKey, territory).map(o => ({ ...o }));
  if (list.length <= 1) return; // keep at least one owner in the rotation
  list.splice(index, 1);
  commitList(mapKey, territory, list);
}

function addOwner() {
  const email = newEmail.value.trim();
  const id = newId.value.trim();
  if (!validEmail(email) || !id) {
    useAlert(t('ROUTING_CONFIG.OWNERS.ADD_INVALID'));
    return;
  }
  if (allOwners.value.some(o => o.owner_email === email && o.owner_id === id)) {
    useAlert(t('ROUTING_CONFIG.OWNERS.ADD_DUPLICATE'));
    return;
  }
  extraOwners.push({ owner_email: email, owner_id: id, __added: true });
  newEmail.value = '';
  newId.value = '';
}

function discard() {
  edits.splice(0, edits.length);
  errors.value = [];
}

async function publish() {
  if (busy.value || !dirtyCount.value) return;
  errors.value = [];
  const doc = JSON.parse(JSON.stringify(props.override || {}));
  edits.forEach(({ mapKey, territory, value }) => {
    const v = Array.isArray(value)
      ? value.filter(o => o && o.owner_email)
      : value;
    if (territory === null) {
      doc[mapKey] = v;
    } else {
      doc[mapKey] = doc[mapKey] || {};
      doc[mapKey][territory] = v;
    }
  });

  busy.value = true;
  const base = `/api/v1/accounts/${accountId.value}/integrations/routing_config`;
  try {
    const { data: v } = await axios.post(`${base}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || ['Validation failed.'];
      return;
    }
    await axios.post(`${base}/publish`, {
      doc,
      note: 'CRM owners updated from the UI',
    });
    useAlert(t('ROUTING_CONFIG.OWNERS.PUBLISHED'));
    discard();
    emit('published');
  } catch (e) {
    useAlert(
      e?.response?.data?.error || t('ROUTING_CONFIG.OWNERS.PUBLISH_FAILED')
    );
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="pb-24">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.OWNERS.EDIT_HINT') }}
    </p>

    <!-- Add an owner to the picker -->
    <div class="p-3 mb-5 border rounded-xl border-n-weak bg-n-alpha-1">
      <div class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.OWNERS.ADD_TITLE') }}
      </div>
      <div class="mb-2 text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.OWNERS.ADD_SUB') }}
      </div>
      <div class="flex flex-wrap gap-2">
        <input
          v-model="newEmail"
          type="email"
          :placeholder="t('ROUTING_CONFIG.OWNERS.ADD_EMAIL')"
          class="flex-1 min-w-[12rem] px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        />
        <input
          v-model="newId"
          type="text"
          inputmode="numeric"
          :placeholder="t('ROUTING_CONFIG.OWNERS.ADD_ID')"
          class="w-40 px-2.5 py-1.5 font-mono text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        />
        <button
          type="button"
          class="px-3 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90"
          @click="addOwner"
        >
          {{ t('ROUTING_CONFIG.OWNERS.ADD_BTN') }}
        </button>
      </div>
    </div>

    <!-- Owner maps -->
    <div v-for="m in maps" :key="m.key" class="mb-6">
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-sm font-semibold text-n-slate-12">{{ m.label }}</h3>
        <span
          v-if="m.kind === 'list'"
          class="px-2 py-0.5 text-[0.65rem] font-medium rounded-full bg-n-amber-2 text-n-amber-11"
        >
          {{ t('ROUTING_CONFIG.OWNERS.ROTATION_BADGE') }}
        </span>
      </div>

      <!-- flat single owner -->
      <div v-if="m.kind === 'flat'" class="max-w-md">
        <select
          class="w-full px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
          :value="currentEmail(m.key, null)"
          @change="reassign(m.key, null, $event.target.value)"
        >
          <option value="" disabled>
            {{ t('ROUTING_CONFIG.OWNERS.CHOOSE') }}
          </option>
          <option
            v-for="o in allOwners"
            :key="o.owner_email"
            :value="o.owner_email"
          >
            {{ o.owner_email }}
          </option>
        </select>
      </div>

      <!-- map: territory -> single owner -->
      <div
        v-else-if="m.kind === 'map'"
        class="overflow-x-auto border rounded-xl border-n-weak"
      >
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-n-slate-11 bg-n-alpha-1">
              <th class="px-3 py-2 font-medium w-1/2">
                {{ t('ROUTING_CONFIG.OWNERS.COL_TERRITORY') }}
              </th>
              <th class="px-3 py-2 font-medium">
                {{ t('ROUTING_CONFIG.OWNERS.COL_OWNER') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ter in territories(m.key)"
              :key="ter"
              class="border-t border-n-weak"
            >
              <td class="px-3 py-2 align-middle text-n-slate-12">{{ ter }}</td>
              <td class="px-3 py-2">
                <select
                  class="w-full max-w-sm px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                  :class="findEdit(m.key, ter) ? 'border-n-brand' : ''"
                  :value="currentEmail(m.key, ter)"
                  @change="reassign(m.key, ter, $event.target.value)"
                >
                  <option value="" disabled>
                    {{ t('ROUTING_CONFIG.OWNERS.CHOOSE') }}
                  </option>
                  <option
                    v-for="o in allOwners"
                    :key="o.owner_email"
                    :value="o.owner_email"
                  >
                    {{ o.owner_email }}
                  </option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- list (round-robin): editable rotation -->
      <div v-else class="overflow-x-auto border rounded-xl border-n-weak">
        <div class="px-3 py-2 text-xs text-n-slate-10 bg-n-alpha-1">
          {{ t('ROUTING_CONFIG.OWNERS.ROTATION_HINT') }}
        </div>
        <table class="w-full text-sm">
          <tbody>
            <tr
              v-for="ter in territories(m.key)"
              :key="ter"
              class="border-t border-n-weak align-top"
            >
              <td class="w-1/3 px-3 py-3 text-n-slate-12">{{ ter }}</td>
              <td class="px-3 py-3">
                <div class="flex flex-col gap-2">
                  <div
                    v-for="(o, idx) in currentList(m.key, ter)"
                    :key="idx"
                    class="flex items-center gap-2"
                  >
                    <select
                      class="flex-1 max-w-sm px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                      :class="findEdit(m.key, ter) ? 'border-n-brand' : ''"
                      :value="o.owner_email"
                      @change="
                        setListOwner(m.key, ter, idx, $event.target.value)
                      "
                    >
                      <option value="" disabled>
                        {{ t('ROUTING_CONFIG.OWNERS.CHOOSE') }}
                      </option>
                      <option
                        v-for="opt in allOwners"
                        :key="opt.owner_email"
                        :value="opt.owner_email"
                      >
                        {{ opt.owner_email }}
                      </option>
                    </select>
                    <button
                      type="button"
                      class="px-2 py-1 text-xs rounded-lg text-n-ruby-11 hover:bg-n-ruby-2 disabled:opacity-40"
                      :disabled="currentList(m.key, ter).length <= 1"
                      @click="removeListOwner(m.key, ter, idx)"
                    >
                      {{ t('ROUTING_CONFIG.OWNERS.REMOVE') }}
                    </button>
                  </div>
                  <button
                    type="button"
                    class="self-start px-2 py-1 text-xs font-medium rounded-lg text-n-brand hover:bg-n-alpha-1"
                    @click="addListOwner(m.key, ter)"
                  >
                    {{ t('ROUTING_CONFIG.OWNERS.ADD_TO_ROTATION') }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- validation errors -->
    <div
      v-if="errors.length"
      class="p-3 mb-4 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <div class="mb-1 font-medium">
        {{ t('ROUTING_CONFIG.OWNERS.VALIDATION_FAILED') }}
      </div>
      <ul class="pl-4 list-disc">
        <li v-for="(er, i) in errors" :key="i">{{ er }}</li>
      </ul>
    </div>

    <!-- sticky action bar -->
    <div
      v-if="dirtyCount"
      class="fixed bottom-0 inset-x-0 z-10 border-t border-n-weak bg-n-surface/95 backdrop-blur"
    >
      <div
        class="flex items-center justify-end max-w-5xl gap-3 px-6 py-3 mx-auto"
      >
        <span class="mr-auto text-sm text-n-slate-11">
          {{ t('ROUTING_CONFIG.OWNERS.DIRTY', { count: dirtyCount }) }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy"
          @click="discard"
        >
          {{ t('ROUTING_CONFIG.OWNERS.DISCARD') }}
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="publish"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.OWNERS.PUBLISHING')
              : t('ROUTING_CONFIG.OWNERS.PUBLISH')
          }}
        </button>
      </div>
    </div>
  </div>
</template>

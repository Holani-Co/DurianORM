<script setup>
// Review Categories settings screen (via the admin Rails proxy). Per vertical:
// the categories the AI sorts a Google review into before picking a reply.
// Built-in ones (review_reply_bank.yaml) can be described or switched off; new
// ones are added here with a "use when" description and a first reply. Replies
// themselves are canned responses review_<vertical>_<category>_NN.
import { ref, reactive, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import Switch from 'dashboard/components-next/switch/Switch.vue';
import {
  REVIEW_VERTICALS,
  parseReviewCode,
  prettyReviewCategory,
} from 'dashboard/helper/templateTaxonomy';

const { t } = useI18n();
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');
const cannedResponses = useMapGetter('getCannedResponses');
const axios = window.axios;

const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/review_categories_config`;

const loading = ref(true);
const error = ref(false);
const busy = ref(false);
const errors = ref([]);
const builtin = ref({});
const descMax = ref(300);
const vertical = ref('furniture');
// Working copy of the override: {vertical: {key: {sentiment?, description, disabled}}}
const doc = reactive({});

const loadDoc = override => {
  REVIEW_VERTICALS.forEach(({ value }) => {
    doc[value] = JSON.parse(JSON.stringify(override?.[value] || {}));
  });
};

const fetchAll = async () => {
  loading.value = true;
  error.value = false;
  try {
    const [{ data }] = await Promise.all([
      axios.get(base()),
      store.dispatch('getCannedResponse'),
    ]);
    builtin.value = data.builtin || {};
    descMax.value = data.description_max || 300;
    loadDoc(data.override);
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
};

onMounted(fetchAll);

// Canned responses of each category of the selected vertical: {key: [response]}.
const repliesByKey = computed(() => {
  const out = {};
  (cannedResponses.value || []).forEach(cr => {
    const { vertical: v, category } = parseReviewCode(cr.short_code);
    if (v === vertical.value && category) {
      (out[category] = out[category] || []).push(cr);
    }
  });
  return out;
});

const rows = computed(() => {
  const v = vertical.value;
  const own = doc[v] || {};
  const builtinKeys = Object.keys(builtin.value[v] || {});
  const customKeys = Object.keys(own).filter(k => !builtinKeys.includes(k));
  return [...builtinKeys, ...customKeys].map(key => ({
    key,
    label: prettyReviewCategory(key),
    sentiment: key.split('_')[0],
    builtin: builtinKeys.includes(key),
    replies: (repliesByKey.value[key] || []).length,
  }));
});

const entry = key => {
  const v = vertical.value;
  if (!doc[v][key]) doc[v][key] = {};
  return doc[v][key];
};
const isOn = key => !(doc[vertical.value][key] || {}).disabled;
const setOn = (key, on) => {
  entry(key).disabled = !on;
};

// Drop empty entries for built-in categories (no description, not switched off).
const buildDoc = () => {
  const out = {};
  REVIEW_VERTICALS.forEach(({ value: v }) => {
    const builtinKeys = Object.keys(builtin.value[v] || {});
    Object.entries(doc[v] || {}).forEach(([key, c]) => {
      const description = (c.description || '').trim();
      const isBuiltin = builtinKeys.includes(key);
      if (isBuiltin && !description && !c.disabled) return;
      out[v] = out[v] || {};
      out[v][key] = {
        ...(isBuiltin ? {} : { sentiment: key.split('_')[0] }),
        description,
        disabled: Boolean(c.disabled),
      };
    });
  });
  return out;
};

const validate = async payload => {
  const { data } = await axios.post(`${base()}/validate`, { doc: payload });
  errors.value = data.ok ? [] : data.errors || [];
  return data.ok;
};

const publish = async (payload, note) => {
  await axios.post(`${base()}/publish`, { doc: payload, note });
};

async function save() {
  if (busy.value) return;
  busy.value = true;
  try {
    const payload = buildDoc();
    if (!(await validate(payload))) return;
    await publish(payload, t('REVIEW_CATEGORIES_CONFIG.PUBLISH_NOTE'));
    useAlert(t('REVIEW_CATEGORIES_CONFIG.SAVED'));
    await fetchAll();
  } catch (e) {
    useAlert(t('REVIEW_CATEGORIES_CONFIG.SAVE_FAILED'));
  } finally {
    busy.value = false;
  }
}

// ── Add a category ─────────────────────────────────────────────────────────
const form = reactive({
  name: '',
  sentiment: 'positive',
  description: '',
  reply: '',
});
const slug = s =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
const newKey = computed(() =>
  form.name.trim() ? `${form.sentiment}_${slug(form.name)}` : ''
);
const canAdd = computed(
  () =>
    newKey.value &&
    form.description.trim() &&
    form.reply.trim() &&
    !rows.value.some(r => r.key === newKey.value)
);

async function addCategory() {
  if (busy.value || !canAdd.value) return;
  busy.value = true;
  const key = newKey.value;
  const v = vertical.value;
  try {
    doc[v][key] = {
      sentiment: form.sentiment,
      description: form.description.trim(),
      disabled: false,
    };
    const payload = buildDoc();
    if (!(await validate(payload))) {
      delete doc[v][key];
      return;
    }
    await store.dispatch('createCannedResponse', {
      short_code: `review_${v}_${key}_01`,
      content: form.reply.trim(),
    });
    await publish(payload, t('REVIEW_CATEGORIES_CONFIG.ADD_NOTE', { key }));
    Object.assign(form, { name: '', description: '', reply: '' });
    useAlert(t('REVIEW_CATEGORIES_CONFIG.ADDED'));
    await fetchAll();
  } catch (e) {
    delete doc[v][key];
    useAlert(t('REVIEW_CATEGORIES_CONFIG.SAVE_FAILED'));
  } finally {
    busy.value = false;
  }
}

// ── Remove an added category (and its replies) ────────────────────────────
const confirmingKey = ref(null);

async function removeCategory(key) {
  if (busy.value) return;
  busy.value = true;
  const v = vertical.value;
  const removed = doc[v][key];
  try {
    delete doc[v][key];
    const payload = buildDoc();
    if (!(await validate(payload))) {
      doc[v][key] = removed;
      return;
    }
    await publish(payload, t('REVIEW_CATEGORIES_CONFIG.REMOVE_NOTE', { key }));
    await Promise.all(
      (repliesByKey.value[key] || []).map(cr =>
        store.dispatch('deleteCannedResponse', cr.id)
      )
    );
    useAlert(t('REVIEW_CATEGORIES_CONFIG.REMOVED'));
    await fetchAll();
  } catch (e) {
    doc[v][key] = removed;
    useAlert(t('REVIEW_CATEGORIES_CONFIG.SAVE_FAILED'));
  } finally {
    confirmingKey.value = null;
    busy.value = false;
  }
}
</script>

<template>
  <div class="w-full max-w-4xl">
    <div class="mb-6">
      <h1 class="text-xl font-semibold text-n-slate-12">
        {{ t('REVIEW_CATEGORIES_CONFIG.HEADER') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('REVIEW_CATEGORIES_CONFIG.DESCRIPTION') }}
      </p>
    </div>

    <div v-if="loading" class="py-10 text-sm text-n-slate-11">
      {{ t('REVIEW_CATEGORIES_CONFIG.LOADING') }}
    </div>

    <div
      v-else-if="error"
      class="p-4 text-sm border rounded-lg border-n-weak bg-n-alpha-2 text-n-ruby-11"
    >
      {{ t('REVIEW_CATEGORIES_CONFIG.BRIDGE_UNAVAILABLE') }}
    </div>

    <div v-else>
      <div class="flex gap-1 mb-4 border-b border-n-weak">
        <button
          v-for="v in REVIEW_VERTICALS"
          :key="v.value"
          class="px-3 py-2 -mb-px text-sm border-b-2"
          :class="
            vertical === v.value
              ? 'border-n-brand text-n-slate-12 font-medium'
              : 'border-transparent text-n-slate-11'
          "
          @click="vertical = v.value"
        >
          {{ v.label }}
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

      <div
        class="p-3 mb-4 text-xs border rounded-lg border-n-weak bg-n-alpha-1 text-n-slate-11"
      >
        {{ t('REVIEW_CATEGORIES_CONFIG.REPLIES_HINT') }}
        <router-link
          :to="{ name: 'canned_list', params: { accountId } }"
          class="font-medium underline text-n-brand"
        >
          {{ t('REVIEW_CATEGORIES_CONFIG.REPLIES_LINK') }}
        </router-link>
      </div>

      <div class="flex flex-col gap-3">
        <div
          v-for="row in rows"
          :key="row.key"
          class="p-3 border rounded-lg border-n-weak"
          :class="isOn(row.key) ? '' : 'opacity-60'"
        >
          <div class="flex flex-wrap items-center gap-2">
            <Switch
              :model-value="isOn(row.key)"
              @update:model-value="on => setOn(row.key, on)"
            />
            <span class="text-sm font-medium text-n-slate-12">
              {{ row.label }}
            </span>
            <span
              class="px-2 py-0.5 text-xs rounded-full"
              :class="
                row.sentiment === 'positive'
                  ? 'bg-n-teal-3 text-n-teal-11'
                  : 'bg-n-ruby-3 text-n-ruby-11'
              "
            >
              {{
                row.sentiment === 'positive'
                  ? t('REVIEW_CATEGORIES_CONFIG.POSITIVE')
                  : t('REVIEW_CATEGORIES_CONFIG.NEGATIVE')
              }}
            </span>
            <span
              v-if="!row.builtin"
              class="px-2 py-0.5 text-xs rounded-full bg-n-blue-3 text-n-blue-11"
            >
              {{ t('REVIEW_CATEGORIES_CONFIG.ADDED_BADGE') }}
            </span>
            <span
              class="text-xs"
              :class="row.replies ? 'text-n-slate-11' : 'text-n-amber-11'"
            >
              {{
                row.replies
                  ? t('REVIEW_CATEGORIES_CONFIG.REPLY_COUNT', {
                      count: row.replies,
                    })
                  : t('REVIEW_CATEGORIES_CONFIG.NO_REPLIES')
              }}
            </span>
            <span class="flex-1" />
            <template v-if="!row.builtin">
              <button
                v-if="confirmingKey !== row.key"
                type="button"
                class="text-xs text-n-ruby-11 hover:underline"
                :disabled="busy"
                @click="confirmingKey = row.key"
              >
                {{ t('REVIEW_CATEGORIES_CONFIG.REMOVE') }}
              </button>
              <template v-else>
                <span class="text-xs text-n-slate-11">
                  {{
                    t('REVIEW_CATEGORIES_CONFIG.CONFIRM_REMOVE', {
                      count: row.replies,
                    })
                  }}
                </span>
                <button
                  type="button"
                  class="text-xs font-medium text-n-ruby-11 hover:underline"
                  :disabled="busy"
                  @click="removeCategory(row.key)"
                >
                  {{ t('REVIEW_CATEGORIES_CONFIG.REMOVE') }}
                </button>
                <button
                  type="button"
                  class="text-xs text-n-slate-11 hover:underline"
                  @click="confirmingKey = null"
                >
                  {{ t('REVIEW_CATEGORIES_CONFIG.CANCEL') }}
                </button>
              </template>
            </template>
          </div>
          <input
            :value="(doc[vertical][row.key] || {}).description || ''"
            type="text"
            :maxlength="descMax"
            :placeholder="t('REVIEW_CATEGORIES_CONFIG.USE_WHEN_PLACEHOLDER')"
            class="w-full px-3 py-1.5 mt-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            @input="entry(row.key).description = $event.target.value"
          />
        </div>
      </div>

      <div class="sticky bottom-0 flex justify-end py-3 mt-2 bg-n-background">
        <button
          type="button"
          class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="save"
        >
          {{
            busy
              ? t('REVIEW_CATEGORIES_CONFIG.SAVING')
              : t('REVIEW_CATEGORIES_CONFIG.SAVE')
          }}
        </button>
      </div>

      <div class="p-4 mt-6 border rounded-lg border-n-weak">
        <h2 class="mb-1 text-base font-semibold text-n-slate-12">
          {{ t('REVIEW_CATEGORIES_CONFIG.ADD_TITLE') }}
        </h2>
        <p class="mb-3 text-xs text-n-slate-11">
          {{ t('REVIEW_CATEGORIES_CONFIG.ADD_HINT') }}
        </p>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label class="flex flex-col gap-1 text-xs text-n-slate-11 md:col-span-2">
            {{ t('REVIEW_CATEGORIES_CONFIG.NAME') }}
            <input
              v-model="form.name"
              type="text"
              maxlength="40"
              :placeholder="t('REVIEW_CATEGORIES_CONFIG.NAME_PLACEHOLDER')"
              class="px-3 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            />
          </label>
          <label class="flex flex-col gap-1 text-xs text-n-slate-11">
            {{ t('REVIEW_CATEGORIES_CONFIG.SENTIMENT') }}
            <select
              v-model="form.sentiment"
              class="px-3 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            >
              <option value="positive">
                {{ t('REVIEW_CATEGORIES_CONFIG.POSITIVE') }}
              </option>
              <option value="negative">
                {{ t('REVIEW_CATEGORIES_CONFIG.NEGATIVE') }}
              </option>
            </select>
          </label>
          <label class="flex flex-col gap-1 text-xs text-n-slate-11 md:col-span-3">
            {{ t('REVIEW_CATEGORIES_CONFIG.USE_WHEN') }}
            <input
              v-model="form.description"
              type="text"
              :maxlength="descMax"
              :placeholder="t('REVIEW_CATEGORIES_CONFIG.USE_WHEN_EXAMPLE')"
              class="px-3 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            />
          </label>
          <label class="flex flex-col gap-1 text-xs text-n-slate-11 md:col-span-3">
            {{ t('REVIEW_CATEGORIES_CONFIG.FIRST_REPLY') }}
            <textarea
              v-model="form.reply"
              rows="3"
              :placeholder="t('REVIEW_CATEGORIES_CONFIG.FIRST_REPLY_PLACEHOLDER')"
              class="px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            />
          </label>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2 mt-3">
          <span v-if="newKey" class="text-xs text-n-slate-11">
            {{ t('REVIEW_CATEGORIES_CONFIG.CODE_PREVIEW') }}
            <code>{{ `review_${vertical}_${newKey}_01` }}</code>
          </span>
          <span v-else />
          <button
            type="button"
            class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
            :disabled="busy || !canAdd"
            @click="addCategory"
          >
            {{ t('REVIEW_CATEGORIES_CONFIG.ADD') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// Phase 3 — editable categories & keywords. Each category is a collapsible card;
// edit its display name, whether it stays in the inbox or forwards (and where),
// and its keywords. The description + example messages teach the AI classifier and
// live behind an "advanced" section with a caution. Edits are collected as a draft
// and published to the bridge in one go (validate -> publish -> live). Only the
// changed fields are sent, so untouched fields keep coming from the YAML defaults.
import { ref, reactive, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  effective: { type: Object, default: () => ({}) },
  override: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['published']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const catEdits = reactive({}); // { catKey: { changedField: value } }
const kwInput = reactive({}); // { catKey: 'draft keyword' }
const newKeys = reactive([]); // categories added this session, not yet published
const busy = ref(false);
const errors = ref([]);

const showAdd = ref(false);
const addForm = reactive({
  key: '',
  name: '',
  action: 'in_channel',
  forwardEmails: [],   // multiple forward-to emails
  forwardInput: '',    // draft input for the forward chip input
  ccEmails: [],        // multiple CC emails
  ccInput: '',         // draft input for the CC chip input
  desc: '',
});

// Draft inputs for existing category forward/cc chip inputs
const fwdInput = reactive({});  // { catKey: 'draft email' }
const ccInput = reactive({});   // { catKey: 'draft email' }

const validEmail = e => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((e || '').trim());

// New categories are listed first so they're obvious after adding.
const categoryKeys = computed(() => [
  ...newKeys,
  ...Object.keys(props.effective.categories || {}),
]);
const dirtyCount = computed(() => Object.keys(catEdits).length);
const isEdited = key => key in catEdits;
const isNew = key => newKeys.includes(key);

function resetAddForm() {
  Object.assign(addForm, {
    key: '',
    name: '',
    action: 'in_channel',
    forwardEmail: '',
    ccEmails: [],
    ccInput: '',
    desc: '',
    acknowledgeCustomer: true,
    includeCustomerInCc: false,
  });
}

// ── Add-form email chip helpers ──
function addCcEmailToForm() {
  const email = addForm.ccInput.trim();
  if (!email) return;
  if (!validEmail(email)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.INVALID_EMAIL'));
    return;
  }
  if (addForm.ccEmails.includes(email)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.DUPLICATE_EMAIL'));
    addForm.ccInput = '';
    return;
  }
  addForm.ccEmails.push(email);
  addForm.ccInput = '';
}
function removeCcEmailFromForm(idx) {
  addForm.ccEmails.splice(idx, 1);
}

function confirmAdd() {
  const key = addForm.key.trim();
  const name = addForm.name.trim();
  if (!/^[a-z][a-z0-9_]*$/.test(key)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.KEY_INVALID'));
    return;
  }
  if (categoryKeys.value.includes(key)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.KEY_DUPLICATE'));
    return;
  }
  if (!name) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.NAME_REQUIRED'));
    return;
  }
  if (addForm.action === 'forward') {
    if (!addForm.forwardEmail.trim()) {
      useAlert(t('ROUTING_CONFIG.CATEGORIES.FORWARD_REQUIRED'));
      return;
    }
    if (!validEmail(addForm.forwardEmail.trim())) {
      useAlert(t('ROUTING_CONFIG.CATEGORIES.INVALID_EMAIL'));
      return;
    }
  }
  const value = {
    display_name: name,
    action: addForm.action,
    description: addForm.desc.trim(),
    keywords: [],
    acknowledge_customer: addForm.acknowledgeCustomer,
  };
  if (addForm.action === 'forward') {
    value.forward_to = addForm.forwardEmail.trim();
    value.cc = [...addForm.ccEmails];
    value.include_customer_in_cc = addForm.includeCustomerInCc;
  }
  newKeys.unshift(key);
  catEdits[key] = value;
  resetAddForm();
  showAdd.value = false;
}

function cancelAdd() {
  resetAddForm();
  showAdd.value = false;
}

function base(key) {
  return (props.effective.categories || {})[key] || {};
}
function field(key, name) {
  if (catEdits[key] && name in catEdits[key]) return catEdits[key][name];
  return base(key)[name];
}
function setField(key, name, value) {
  catEdits[key] = { ...(catEdits[key] || {}), [name]: value };
}

// ── CC emails for existing categories ──
function ccEmails(key) {
  return field(key, 'cc') || [];
}
function addCcEmailForKey(key) {
  const email = (ccInput[key] || '').trim();
  if (!email) return;
  if (!validEmail(email)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.INVALID_EMAIL'));
    return;
  }
  const existing = ccEmails(key);
  if (existing.includes(email)) {
    useAlert(t('ROUTING_CONFIG.CATEGORIES.DUPLICATE_EMAIL'));
    ccInput[key] = '';
    return;
  }
  setField(key, 'cc', [...existing, email]);
  ccInput[key] = '';
}
function removeCcEmailForKey(key, idx) {
  const arr = [...ccEmails(key)];
  arr.splice(idx, 1);
  setField(key, 'cc', arr);
}

function keywords(key) {
  return field(key, 'keywords') || [];
}
function addKeyword(key) {
  const kw = (kwInput[key] || '').trim();
  if (!kw) return;
  if (keywords(key).includes(kw)) {
    kwInput[key] = '';
    return;
  }
  setField(key, 'keywords', [...keywords(key), kw]);
  kwInput[key] = '';
}
function removeKeyword(key, idx) {
  const arr = [...keywords(key)];
  arr.splice(idx, 1);
  setField(key, 'keywords', arr);
}

function examplesText(key) {
  return (field(key, 'examples') || []).join('\n');
}
function setExamples(key, text) {
  setField(key, 'examples', text.split('\n'));
}

function discard() {
  Object.keys(catEdits).forEach(k => delete catEdits[k]);
  newKeys.splice(0, newKeys.length);
  errors.value = [];
}

async function publish() {
  if (busy.value || !dirtyCount.value) return;
  errors.value = [];
  const doc = JSON.parse(JSON.stringify(props.override || {}));
  doc.categories = doc.categories || {};
  Object.keys(catEdits).forEach(key => {
    const changed = { ...catEdits[key] };
    if (Array.isArray(changed.keywords)) {
      changed.keywords = changed.keywords.map(k => k.trim()).filter(Boolean);
    }
    if (Array.isArray(changed.examples)) {
      changed.examples = changed.examples.map(e => e.trim()).filter(Boolean);
    }
    doc.categories[key] = { ...(doc.categories[key] || {}), ...changed };
  });

  busy.value = true;
  const url = `/api/v1/accounts/${accountId.value}/integrations/routing_config`;
  try {
    const { data: v } = await axios.post(`${url}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || ['Validation failed.'];
      return;
    }
    await axios.post(`${url}/publish`, {
      doc,
      note: 'Categories updated from the UI',
    });
    useAlert(t('ROUTING_CONFIG.CATEGORIES.PUBLISHED'));
    discard();
    emit('published');
  } catch (e) {
    useAlert(
      e?.response?.data?.error || t('ROUTING_CONFIG.CATEGORIES.PUBLISH_FAILED')
    );
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="pb-24">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.CATEGORIES.EDIT_HINT') }}
    </p>

    <!-- Add category -->
    <div class="mb-4">
      <button
        v-if="!showAdd"
        type="button"
        class="px-3 py-1.5 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
        @click="showAdd = true"
      >
        {{ t('ROUTING_CONFIG.CATEGORIES.ADD_CATEGORY') }}
      </button>
      <div v-else class="p-4 border rounded-xl border-n-brand bg-n-alpha-1">
        <div class="text-sm font-medium text-n-slate-12">
          {{ t('ROUTING_CONFIG.CATEGORIES.ADD_TITLE') }}
        </div>
        <div class="mb-3 text-xs text-n-slate-10">
          {{ t('ROUTING_CONFIG.CATEGORIES.ADD_SUB') }}
        </div>
        <div class="flex flex-col gap-3">
          <div class="flex flex-wrap gap-3">
            <label class="flex flex-col flex-1 gap-1 min-w-[12rem]">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.KEY_LABEL')
              }}</span>
              <input
                v-model="addForm.key"
                type="text"
                :placeholder="t('ROUTING_CONFIG.CATEGORIES.KEY_PH')"
                class="px-2.5 py-1.5 font-mono text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              />
              <span class="text-xs text-n-slate-10">{{
                t('ROUTING_CONFIG.CATEGORIES.KEY_HINT')
              }}</span>
            </label>
            <label class="flex flex-col flex-1 gap-1 min-w-[12rem]">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.DISPLAY_NAME')
              }}</span>
              <input
                v-model="addForm.name"
                type="text"
                class="px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              />
            </label>
          </div>
          <div class="flex flex-wrap items-start gap-3">
            <label class="flex flex-col gap-1 min-w-[10rem]">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.COL_ACTION')
              }}</span>
              <select
                v-model="addForm.action"
                class="routing-select px-2.5 pr-8 py-1 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand cursor-pointer w-full"
              >
                <option value="in_channel">
                  {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL') }}
                </option>
                <option value="forward">
                  {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD') }}
                </option>
              </select>
            </label>
            <div
              v-if="addForm.action === 'forward'"
              class="flex flex-col flex-1 gap-3 min-w-[14rem]"
            >
              <div class="flex flex-col gap-1">
                <span class="text-xs font-medium text-n-slate-11">{{
                  t('ROUTING_CONFIG.CATEGORIES.COL_FORWARD')
                }}</span>
                <input
                  v-model="addForm.forwardEmail"
                  type="email"
                  :placeholder="t('ROUTING_CONFIG.CATEGORIES.FORWARD_TO_PH')"
                  class="w-full px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                />
              </div>
              <div class="flex flex-col gap-1">
                <span class="text-xs font-medium text-n-slate-11">{{
                  t('ROUTING_CONFIG.CATEGORIES.CC_LABEL')
                }}</span>
                <div class="flex flex-wrap items-center gap-1.5 px-2.5 py-1 border rounded-lg border-n-weak bg-n-surface min-h-[2rem] focus-within:border-n-brand">
                  <span
                    v-for="(email, idx) in addForm.ccEmails"
                    :key="'cc-add-' + idx"
                    class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
                  >
                    {{ email }}
                    <button
                      type="button"
                      class="flex text-n-slate-10 hover:text-n-ruby-11"
                      @click="removeCcEmailFromForm(idx)"
                    >
                      <span class="i-lucide-x text-[0.7rem]" aria-hidden="true" />
                    </button>
                  </span>
                  <input
                    v-model="addForm.ccInput"
                    type="email"
                    :placeholder="t('ROUTING_CONFIG.CATEGORIES.CC_PH')"
                    class="email-chips-input flex-1 min-w-[10rem] text-xs text-n-slate-12 placeholder:text-n-slate-10"
                    @keydown.enter.prevent="addCcEmailToForm"
                  />
                </div>
              </div>
            </div>
          </div>
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-n-slate-11">{{
              t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_LABEL')
            }}</span>
            <textarea
              v-model="addForm.desc"
              rows="3"
              class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
            />
          </label>
          <div class="flex flex-col gap-2 mt-2 mb-2">
            <label class="flex items-center gap-2 cursor-pointer">
              <input
                v-model="addForm.acknowledgeCustomer"
                type="checkbox"
                class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
              />
              <span class="text-xs font-medium text-n-slate-11">
                Acknowledge Customer
              </span>
            </label>
            <label
              v-if="addForm.action === 'forward'"
              class="flex items-center gap-2 cursor-pointer"
            >
              <input
                v-model="addForm.includeCustomerInCc"
                type="checkbox"
                class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
              />
              <span class="text-xs font-medium text-n-slate-11">
                Include Customer in CC
              </span>
            </label>
          </div>
          <div class="flex gap-2">
            <button
              type="button"
              class="px-3 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90"
              @click="confirmAdd"
            >
              {{ t('ROUTING_CONFIG.CATEGORIES.ADD_CONFIRM') }}
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
              @click="cancelAdd"
            >
              {{ t('ROUTING_CONFIG.CATEGORIES.ADD_CANCEL') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <details
      v-for="key in categoryKeys"
      :key="key"
      class="mb-2 border rounded-xl border-n-weak"
      :class="isEdited(key) ? 'border-n-brand' : ''"
    >
      <summary
        class="flex flex-wrap items-center gap-2 px-4 py-3 cursor-pointer"
      >
        <span class="font-medium text-n-slate-12">{{
          field(key, 'display_name') || key
        }}</span>
        <span
          v-if="isNew(key)"
          class="px-1.5 py-0.5 text-[0.65rem] font-medium rounded-full bg-n-alpha-2 text-n-brand"
        >
          {{ t('ROUTING_CONFIG.CATEGORIES.NEW_BADGE') }}
        </span>
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full"
          :class="
            field(key, 'action') === 'forward'
              ? 'bg-n-amber-2 text-n-amber-11'
              : 'bg-n-teal-3 text-n-teal-11'
          "
        >
          {{
            field(key, 'action') === 'forward'
              ? t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD')
              : t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL')
          }}
        </span>
        <span
          v-if="field(key, 'action') === 'forward' && field(key, 'forward_to')"
          class="text-xs text-n-slate-10"
        >
          {{ field(key, 'forward_to') }}
        </span>
        <span
          v-if="isEdited(key)"
          class="w-2 h-2 ml-auto rounded-full bg-n-brand"
          aria-hidden="true"
        />
      </summary>

      <div class="flex flex-col gap-3 p-4 border-t border-n-weak">
        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">{{
            t('ROUTING_CONFIG.CATEGORIES.DISPLAY_NAME')
          }}</span>
          <input
            type="text"
            :value="field(key, 'display_name')"
            class="max-w-sm px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
            @input="setField(key, 'display_name', $event.target.value)"
          />
        </label>

        <div class="flex flex-wrap items-start gap-3">
          <label class="flex flex-col gap-1 min-w-[10rem]">
            <span class="text-xs font-medium text-n-slate-11">{{
              t('ROUTING_CONFIG.CATEGORIES.COL_ACTION')
            }}</span>
            <select
              :value="field(key, 'action') || 'in_channel'"
              class="routing-select px-2.5 pr-8 py-1 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand cursor-pointer w-full"
              @change="setField(key, 'action', $event.target.value)"
            >
              <option value="in_channel">
                {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL') }}
              </option>
              <option value="forward">
                {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD') }}
              </option>
            </select>
          </label>
          <div
            v-if="field(key, 'action') === 'forward'"
            class="flex flex-col flex-1 gap-3 min-w-[14rem]"
          >
            <div class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.COL_FORWARD')
              }}</span>
              <input
                :value="field(key, 'forward_to') || ''"
                type="email"
                :placeholder="t('ROUTING_CONFIG.CATEGORIES.FORWARD_TO_PH')"
                class="w-full px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                @input="setField(key, 'forward_to', $event.target.value)"
              />
            </div>
            <div class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.CC_LABEL')
              }}</span>
              <div class="flex flex-wrap items-center gap-1.5 px-2.5 py-1 border rounded-lg border-n-weak bg-n-surface min-h-[2rem] focus-within:border-n-brand">
                <span
                  v-for="(email, idx) in ccEmails(key)"
                  :key="'cc-' + key + '-' + idx"
                  class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
                >
                  {{ email }}
                  <button
                    type="button"
                    class="flex text-n-slate-10 hover:text-n-ruby-11"
                    @click="removeCcEmailForKey(key, idx)"
                  >
                    <span class="i-lucide-x text-[0.7rem]" aria-hidden="true" />
                  </button>
                </span>
                <input
                  v-model="ccInput[key]"
                  type="email"
                  :placeholder="t('ROUTING_CONFIG.CATEGORIES.CC_PH')"
                  class="email-chips-input flex-1 min-w-[10rem] text-xs text-n-slate-12 placeholder:text-n-slate-10"
                  @keydown.enter.prevent="addCcEmailForKey(key)"
                />
              </div>
            </div>
          </div>
        </div>

        <div class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">{{
            t('ROUTING_CONFIG.CATEGORIES.KEYWORDS_LABEL')
          }}</span>
          <div class="flex flex-wrap items-center gap-1.5">
            <span
              v-for="(kw, idx) in keywords(key)"
              :key="idx"
              class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
            >
              {{ kw }}
              <button
                type="button"
                :title="t('ROUTING_CONFIG.OWNERS.REMOVE')"
                class="flex text-n-slate-10 hover:text-n-ruby-11"
                @click="removeKeyword(key, idx)"
              >
                <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
              </button>
            </span>
            <input
              v-model="kwInput[key]"
              type="text"
              :placeholder="t('ROUTING_CONFIG.CATEGORIES.ADD_KEYWORD_PH')"
              class="min-w-[12rem] flex-1 px-2 py-1 text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              @keydown.enter.prevent="addKeyword(key)"
            />
          </div>
        </div>

        <details class="mt-1">
          <summary class="text-xs font-medium cursor-pointer text-n-amber-11">
            {{ t('ROUTING_CONFIG.CATEGORIES.ADVANCED') }}
          </summary>
          <div class="flex flex-col gap-3 mt-3">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_LABEL')
              }}</span>
              <span class="text-xs text-n-slate-10">{{
                t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_HINT')
              }}</span>
              <textarea
                :value="field(key, 'description')"
                rows="4"
                class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                @input="setField(key, 'description', $event.target.value)"
              />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_LABEL')
              }}</span>
              <span class="text-xs text-n-slate-10">{{
                t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_HINT')
              }}</span>
              <textarea
                :value="examplesText(key)"
                rows="4"
                class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                @input="setExamples(key, $event.target.value)"
              />
            </label>
            <div class="flex flex-col gap-2 mt-2 mb-2">
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  :checked="field(key, 'acknowledge_customer') != null ? field(key, 'acknowledge_customer') : true"
                  type="checkbox"
                  class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
                  @change="setField(key, 'acknowledge_customer', $event.target.checked)"
                />
                <span class="text-xs font-medium text-n-slate-11">
                  Acknowledge Customer
                </span>
              </label>
              <label
                v-if="field(key, 'action') === 'forward'"
                class="flex items-center gap-2 cursor-pointer"
              >
                <input
                  :checked="field(key, 'include_customer_in_cc') != null ? field(key, 'include_customer_in_cc') : false"
                  type="checkbox"
                  class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
                  @change="setField(key, 'include_customer_in_cc', $event.target.checked)"
                />
                <span class="text-xs font-medium text-n-slate-11">
                  Include Customer in CC
                </span>
              </label>
            </div>
          </div>
        </details>
      </div>
    </details>

    <!-- validation errors -->
    <div
      v-if="errors.length"
      class="p-3 mt-4 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <div class="mb-1 font-medium">
        {{ t('ROUTING_CONFIG.CATEGORIES.VALIDATION_FAILED') }}
      </div>
      <ul class="pl-4 list-disc">
        <li v-for="(er, i) in errors" :key="i">{{ er }}</li>
      </ul>
    </div>

    <!-- sticky action bar -->
    <div
      v-if="dirtyCount"
      class="fixed inset-x-0 bottom-0 z-10 border-t border-n-weak bg-n-surface/95 backdrop-blur"
    >
      <div
        class="flex items-center justify-end max-w-5xl gap-3 px-6 py-3 mx-auto"
      >
        <span class="mr-auto text-sm text-n-slate-11">
          {{ t('ROUTING_CONFIG.CATEGORIES.DIRTY', { count: dirtyCount }) }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy"
          @click="discard"
        >
          {{ t('ROUTING_CONFIG.CATEGORIES.DISCARD') }}
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="publish"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.CATEGORIES.PUBLISHING')
              : t('ROUTING_CONFIG.CATEGORIES.PUBLISH')
          }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Only the dropdown arrow — all colors come from Tailwind classes */
.routing-select {
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.5rem center;
  background-size: 1rem;
}

/* Force override global Chatwoot input styles for the chip input */
.email-chips-input {
  border: none !important;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 0 !important;
  outline: none !important;
  min-height: 0 !important;
  height: auto !important;
}
.email-chips-input:focus {
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
}
</style>

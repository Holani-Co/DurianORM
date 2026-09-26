<script setup>
// Subcategories (product lines) of ONE category: add, rename, switch off, set
// where each one goes, and teach the AI how to recognise it (keywords,
// description, examples) plus the category's own "how to choose a
// subcategory" rules. Holds no draft state itself — every change is emitted to
// CategoriesEditor, which owns the draft and the publish.
import { reactive } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  // [{ key, cfg (effective merged with draft), isNew, isDefault }]
  items: { type: Array, default: () => [] },
  rules: { type: Array, default: () => [] },
  ambiguous: { type: String, default: '' },
});
const emit = defineEmits(['set', 'add', 'setRules', 'setAmbiguous']);

const { t } = useI18n();
const kwInput = reactive({});
const addForm = reactive({ open: false, key: '', name: '' });

const inputClass =
  'w-full px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand';

function set(vkey, name, value) {
  emit('set', vkey, name, value);
}
function lines(text) {
  return (text || '').split('\n');
}

function addKeyword(item) {
  const kw = (kwInput[item.key] || '').trim();
  kwInput[item.key] = '';
  const current = item.cfg.keywords || [];
  if (!kw || current.includes(kw)) return;
  set(item.key, 'keywords', [...current, kw]);
}
function removeKeyword(item, idx) {
  const arr = [...(item.cfg.keywords || [])];
  arr.splice(idx, 1);
  set(item.key, 'keywords', arr);
}
function setCc(item, text) {
  set(
    item.key,
    'cc',
    (text || '')
      .split(',')
      .map(e => e.trim())
      .filter(Boolean)
  );
}

function confirmAdd() {
  const key = addForm.key.trim();
  const name = addForm.name.trim();
  if (!/^[a-z][a-z0-9_]*$/.test(key) || key === 'unclear') {
    useAlert(t('ROUTING_CONFIG.SUBCATEGORIES.KEY_INVALID'));
    return;
  }
  if (props.items.some(i => i.key === key)) {
    useAlert(t('ROUTING_CONFIG.SUBCATEGORIES.KEY_DUPLICATE'));
    return;
  }
  if (!name) {
    useAlert(t('ROUTING_CONFIG.SUBCATEGORIES.NAME_REQUIRED'));
    return;
  }
  emit('add', key, {
    display_name: name,
    keywords: [],
    description: '',
    examples: [],
  });
  Object.assign(addForm, { open: false, key: '', name: '' });
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <div class="flex flex-col gap-0.5">
      <span class="text-xs font-medium text-n-slate-11">
        {{ t('ROUTING_CONFIG.SUBCATEGORIES.LABEL') }}
      </span>
      <span class="text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.SUBCATEGORIES.HINT') }}
      </span>
    </div>

    <details
      v-for="item in items"
      :key="item.key"
      class="border rounded-lg border-n-weak bg-n-alpha-1"
      :open="item.isNew"
    >
      <summary
        class="flex flex-wrap items-center gap-2 px-2.5 py-2 cursor-pointer"
      >
        <span
          class="text-xs font-semibold"
          :class="
            item.cfg.disabled
              ? 'text-n-slate-10 line-through'
              : 'text-n-slate-12'
          "
        >
          {{ item.cfg.display_name || item.key }}
        </span>
        <span class="font-mono text-[0.65rem] text-n-slate-10">{{
          item.key
        }}</span>
        <span
          v-if="item.isNew"
          class="px-1.5 py-0.5 text-[0.65rem] font-medium rounded-full bg-n-alpha-2 text-n-brand"
        >
          {{ t('ROUTING_CONFIG.CATEGORIES.NEW_BADGE') }}
        </span>
        <span
          v-if="item.cfg.disabled"
          class="px-1.5 py-0.5 text-[0.65rem] font-medium rounded-full bg-n-alpha-2 text-n-slate-11"
        >
          {{ t('ROUTING_CONFIG.SUBCATEGORIES.DISABLED_BADGE') }}
        </span>
        <span v-else class="text-[0.7rem] text-n-slate-10">
          {{
            item.cfg.forward_to
              ? t('ROUTING_CONFIG.SUBCATEGORIES.FORWARDS_TO', {
                  to: item.cfg.forward_to,
                })
              : t('ROUTING_CONFIG.SUBCATEGORIES.STAYS')
          }}
        </span>
      </summary>

      <div class="flex flex-col gap-3 p-2.5 border-t border-n-weak">
        <div class="flex flex-wrap items-end gap-3">
          <label class="flex flex-col flex-1 gap-1 min-w-[12rem]">
            <span class="text-xs text-n-slate-11">
              {{ t('ROUTING_CONFIG.CATEGORIES.DISPLAY_NAME') }}
            </span>
            <input
              :value="item.cfg.display_name || ''"
              type="text"
              :class="inputClass"
              @input="set(item.key, 'display_name', $event.target.value)"
            />
          </label>
          <label
            v-if="!item.isDefault"
            class="flex items-center gap-2 pb-2 cursor-pointer"
          >
            <input
              :checked="!item.cfg.disabled"
              type="checkbox"
              class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
              @change="set(item.key, 'disabled', !$event.target.checked)"
            />
            <span class="text-xs font-medium text-n-slate-11">
              {{ t('ROUTING_CONFIG.SUBCATEGORIES.ENABLED') }}
            </span>
          </label>
        </div>

        <!-- Routing -->
        <span v-if="item.isDefault" class="text-xs italic text-n-slate-10">
          {{ t('ROUTING_CONFIG.SUBCATEGORIES.DEFAULT_NOTE') }}
        </span>
        <template v-else>
          <div class="flex flex-wrap gap-3">
            <label class="flex flex-col flex-1 gap-1 min-w-[14rem]">
              <span class="text-xs text-n-slate-11">
                {{ t('ROUTING_CONFIG.CATEGORIES.SUB_FORWARD') }}
              </span>
              <input
                :value="item.cfg.forward_to || ''"
                type="text"
                :placeholder="t('ROUTING_CONFIG.CATEGORIES.SUB_FORWARD_PH')"
                :class="inputClass"
                @input="set(item.key, 'forward_to', $event.target.value)"
              />
              <span class="text-xs text-n-slate-10">
                {{ t('ROUTING_CONFIG.SUBCATEGORIES.FORWARD_HINT') }}
              </span>
            </label>
            <label class="flex flex-col flex-1 gap-1 min-w-[12rem]">
              <span class="text-xs text-n-slate-11">
                {{ t('ROUTING_CONFIG.CATEGORIES.SUB_CC') }}
              </span>
              <input
                :value="(item.cfg.cc || []).join(', ')"
                type="text"
                :placeholder="t('ROUTING_CONFIG.CATEGORIES.SUB_CC_PH')"
                :class="inputClass"
                @input="setCc(item, $event.target.value)"
              />
            </label>
          </div>
          <div
            v-if="item.cfg.forward_to"
            class="flex flex-wrap items-center gap-4"
          >
            <label class="flex items-center gap-2 cursor-pointer">
              <input
                :checked="!!item.cfg.include_customer_in_cc"
                type="checkbox"
                class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
                @change="
                  set(item.key, 'include_customer_in_cc', $event.target.checked)
                "
              />
              <span class="text-xs font-medium text-n-slate-11">
                {{ t('ROUTING_CONFIG.CATEGORIES.INCLUDE_CUSTOMER_CC') }}
              </span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input
                :checked="!!item.cfg.share_executive_email"
                type="checkbox"
                class="w-4 h-4 rounded border-n-weak text-n-brand focus:ring-n-brand"
                @change="
                  set(item.key, 'share_executive_email', $event.target.checked)
                "
              />
              <span class="text-xs font-medium text-n-slate-11">
                {{ t('ROUTING_CONFIG.CATEGORIES.SHARE_EXEC_EMAIL') }}
              </span>
            </label>
          </div>
        </template>

        <!-- Teaching the AI -->
        <div class="flex flex-col gap-1">
          <span class="text-xs text-n-slate-11">
            {{ t('ROUTING_CONFIG.CATEGORIES.KEYWORDS_LABEL') }}
          </span>
          <div class="flex flex-wrap items-center gap-1.5">
            <span
              v-for="(kw, idx) in item.cfg.keywords || []"
              :key="idx"
              class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
            >
              {{ kw }}
              <button
                type="button"
                class="flex text-n-slate-10 hover:text-n-ruby-11"
                @click="removeKeyword(item, idx)"
              >
                <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
              </button>
            </span>
            <input
              v-model="kwInput[item.key]"
              type="text"
              :placeholder="t('ROUTING_CONFIG.CATEGORIES.ADD_KEYWORD_PH')"
              class="min-w-[12rem] flex-1 px-2 py-1 text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              @keydown.enter.prevent="addKeyword(item)"
            />
          </div>
        </div>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-n-slate-11">
            {{ t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_LABEL') }}
          </span>
          <span class="text-xs text-n-slate-10">
            {{ t('ROUTING_CONFIG.SUBCATEGORIES.DESCRIPTION_HINT') }}
          </span>
          <textarea
            :value="item.cfg.description || ''"
            rows="2"
            :class="inputClass + ' resize-y'"
            @input="set(item.key, 'description', $event.target.value)"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-n-slate-11">
            {{ t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_LABEL') }}
          </span>
          <span class="text-xs text-n-slate-10">
            {{ t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_HINT') }}
          </span>
          <textarea
            :value="(item.cfg.examples || []).join('\n')"
            rows="3"
            :class="inputClass + ' resize-y'"
            @input="set(item.key, 'examples', lines($event.target.value))"
          />
        </label>
      </div>
    </details>

    <!-- Add a subcategory -->
    <button
      v-if="!addForm.open"
      type="button"
      class="self-start px-2.5 py-1 text-xs font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
      @click="addForm.open = true"
    >
      {{ t('ROUTING_CONFIG.SUBCATEGORIES.ADD') }}
    </button>
    <div
      v-else
      class="flex flex-wrap items-end gap-3 p-2.5 border rounded-lg border-n-brand"
    >
      <label class="flex flex-col gap-1 min-w-[10rem]">
        <span class="text-xs text-n-slate-11">
          {{ t('ROUTING_CONFIG.CATEGORIES.KEY_LABEL') }}
        </span>
        <input
          v-model="addForm.key"
          type="text"
          :placeholder="t('ROUTING_CONFIG.SUBCATEGORIES.KEY_PH')"
          :class="inputClass + ' font-mono'"
        />
      </label>
      <label class="flex flex-col flex-1 gap-1 min-w-[10rem]">
        <span class="text-xs text-n-slate-11">
          {{ t('ROUTING_CONFIG.CATEGORIES.DISPLAY_NAME') }}
        </span>
        <input v-model="addForm.name" type="text" :class="inputClass" />
      </label>
      <button
        type="button"
        class="px-3 py-1.5 text-xs font-medium rounded-lg bg-n-brand text-white"
        @click="confirmAdd"
      >
        {{ t('ROUTING_CONFIG.SUBCATEGORIES.ADD_CONFIRM') }}
      </button>
      <button
        type="button"
        class="px-3 py-1.5 text-xs font-medium border rounded-lg border-n-weak text-n-slate-11"
        @click="addForm.open = false"
      >
        {{ t('ROUTING_CONFIG.SUBCATEGORIES.CANCEL') }}
      </button>
    </div>

    <!-- How the AI chooses between this category's subcategories -->
    <template v-if="items.length">
      <label class="flex flex-col gap-1 mt-1">
        <span class="text-xs font-medium text-n-slate-11">
          {{ t('ROUTING_CONFIG.SUBCATEGORIES.RULES_LABEL') }}
        </span>
        <span class="text-xs text-n-slate-10">
          {{ t('ROUTING_CONFIG.SUBCATEGORIES.RULES_HINT') }}
        </span>
        <textarea
          :value="rules.join('\n')"
          rows="3"
          :class="inputClass + ' resize-y'"
          @input="emit('setRules', lines($event.target.value))"
        />
      </label>
      <label class="flex flex-col gap-1 max-w-sm">
        <span class="text-xs font-medium text-n-slate-11">
          {{ t('ROUTING_CONFIG.SUBCATEGORIES.UNCLEAR_LABEL') }}
        </span>
        <select
          :value="ambiguous"
          :class="inputClass + ' cursor-pointer'"
          @change="emit('setAmbiguous', $event.target.value)"
        >
          <option value="card">
            {{ t('ROUTING_CONFIG.SUBCATEGORIES.UNCLEAR_CARD') }}
          </option>
          <option value="">
            {{ t('ROUTING_CONFIG.SUBCATEGORIES.UNCLEAR_DEFAULT') }}
          </option>
        </select>
      </label>
    </template>
  </div>
</template>

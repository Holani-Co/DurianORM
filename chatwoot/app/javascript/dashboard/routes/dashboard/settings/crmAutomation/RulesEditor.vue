<script setup>
// The rule cards: stage + field conditions → template, delay and variables.
// Picking a template sets its language and sizes the variable list to it; a
// rule saved against a template that later changed is re-sized on load.
import { computed, watch } from 'vue';
import { useI18n } from 'vue-i18n';

const props = defineProps({
  templates: { type: Array, default: () => [] },
  sources: { type: Object, default: () => ({}) },
  ops: { type: Array, default: () => [] },
});
const rules = defineModel('rules', { type: Array, required: true });

const { t } = useI18n();

const approved = computed(() =>
  props.templates.filter(tp => String(tp.status).toUpperCase() === 'APPROVED')
);
const templateOf = rule => approved.value.find(tp => tp.name === rule.template);

const kindOf = src => {
  if (String(src).startsWith('field:')) return 'field';
  if (String(src).startsWith('text:')) return 'text';
  return src;
};
const argOf = src => String(src).replace(/^(field|text):/, '');

function setKind(variable, kind) {
  variable.source = kind === 'field' || kind === 'text' ? `${kind}:` : kind;
}
function setArg(variable, value) {
  variable.source = `${kindOf(variable.source)}:${value}`;
}

// The house order for missing variables: {{1}} customer, {{2}} sales exec /
// store manager, {{3}} their phone (mirrors STANDARD_VARIABLES in the bridge).
const STANDARD_VARIABLES = [
  { source: 'contact_name', fallback: 'there' },
  { source: 'owner_name', fallback: 'our workspace expert' },
  { source: 'owner_phone', fallback: 'this number' },
];

function fitVariables(rule, count) {
  const vars = rule.variables || [];
  if (vars.length === count) return;
  while (vars.length < count) {
    vars.push({
      ...(STANDARD_VARIABLES[vars.length] || {
        source: 'contact_name',
        fallback: '',
      }),
    });
  }
  vars.length = count;
  rule.variables = vars;
}

function pickTemplate(rule, name) {
  rule.template = name;
  const tpl = approved.value.find(tp => tp.name === name);
  if (!tpl) return;
  rule.language = tpl.language || '';
  fitVariables(rule, tpl.var_count);
}

watch(
  approved,
  () => {
    rules.value.forEach(rule => {
      const tpl = templateOf(rule);
      if (tpl) fitVariables(rule, tpl.var_count);
    });
  },
  { immediate: true }
);

function addRule() {
  rules.value.push({
    id: `rule_${Date.now().toString(36)}`,
    name: '',
    enabled: false,
    stage: '',
    match: [{ field: '', op: 'contains', value: '' }],
    template: '',
    language: '',
    delay_hours: 0,
    only_if_still_in_stage: true,
    header_media_url: '',
    variables: [],
  });
}
const removeRule = i => rules.value.splice(i, 1);
const addFilter = rule => {
  rule.match = rule.match || [];
  rule.match.push({ field: '', op: 'contains', value: '' });
};

const input =
  'px-2 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12';
</script>

<template>
  <div class="flex flex-col gap-3">
    <p v-if="!rules.length" class="text-sm text-n-slate-11">
      {{ t('CRM_AUTOMATION_CONFIG.RULES.EMPTY') }}
    </p>

    <section
      v-for="(rule, i) in rules"
      :key="rule.id"
      class="p-4 border rounded-xl border-n-weak"
      :class="rule.enabled ? '' : 'opacity-80'"
    >
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-sm text-n-slate-12">
          <input v-model="rule.enabled" type="checkbox" />
          {{ t('CRM_AUTOMATION_CONFIG.RULES.ENABLED') }}
        </label>
        <input
          v-model="rule.name"
          type="text"
          :placeholder="t('CRM_AUTOMATION_CONFIG.RULES.NAME')"
          class="flex-1 min-w-[12rem] font-medium"
          :class="input"
        />
        <button
          type="button"
          class="px-2 py-1 text-xs rounded-lg text-n-ruby-11 hover:bg-n-alpha-2"
          @click="removeRule(i)"
        >
          {{ t('CRM_AUTOMATION_CONFIG.RULES.REMOVE') }}
        </button>
      </div>

      <div class="grid gap-3 mt-3 sm:grid-cols-2">
        <label class="text-xs text-n-slate-11">
          {{ t('CRM_AUTOMATION_CONFIG.RULES.STAGE') }}
          <input v-model="rule.stage" type="text" class="w-full mt-1" :class="input" />
        </label>
        <label class="text-xs text-n-slate-11">
          {{ t('CRM_AUTOMATION_CONFIG.RULES.DELAY') }}
          <input
            v-model.number="rule.delay_hours"
            type="number"
            min="0"
            max="720"
            class="w-full mt-1"
            :class="input"
          />
        </label>
      </div>
      <label
        v-if="rule.delay_hours > 0"
        class="flex items-center gap-2 mt-2 text-xs text-n-slate-11"
      >
        <input v-model="rule.only_if_still_in_stage" type="checkbox" />
        {{ t('CRM_AUTOMATION_CONFIG.RULES.STILL_IN_STAGE') }}
      </label>

      <div class="mt-3">
        <div class="text-xs text-n-slate-11">
          {{ t('CRM_AUTOMATION_CONFIG.RULES.FILTERS') }}
        </div>
        <div
          v-for="(m, j) in rule.match"
          :key="j"
          class="flex flex-wrap items-center gap-2 mt-1"
        >
          <input
            v-model="m.field"
            type="text"
            :placeholder="t('CRM_AUTOMATION_CONFIG.RULES.FIELD_PLACEHOLDER')"
            class="w-48"
            :class="input"
          />
          <select v-model="m.op" :class="input">
            <option v-for="op in ops" :key="op" :value="op">
              {{ t(`CRM_AUTOMATION_CONFIG.RULES.OPS.${op}`) }}
            </option>
          </select>
          <input
            v-if="m.op !== 'not_empty'"
            v-model="m.value"
            type="text"
            :placeholder="t('CRM_AUTOMATION_CONFIG.RULES.VALUE_PLACEHOLDER')"
            class="flex-1 min-w-[10rem]"
            :class="input"
          />
          <button
            type="button"
            class="px-2 text-xs text-n-slate-11 hover:text-n-ruby-11"
            @click="rule.match.splice(j, 1)"
          >
            ✕
          </button>
        </div>
        <button
          type="button"
          class="mt-1 text-xs text-n-brand"
          @click="addFilter(rule)"
        >
          {{ t('CRM_AUTOMATION_CONFIG.RULES.ADD_FILTER') }}
        </button>
        <p class="mt-1 text-xs text-n-slate-10">
          {{ t('CRM_AUTOMATION_CONFIG.RULES.FILTERS_HINT') }}
        </p>
      </div>

      <label class="block mt-3 text-xs text-n-slate-11">
        {{ t('CRM_AUTOMATION_CONFIG.RULES.TEMPLATE') }}
        <select
          :value="rule.template"
          class="w-full mt-1"
          :class="input"
          @change="pickTemplate(rule, $event.target.value)"
        >
          <option value="" disabled>
            {{ t('CRM_AUTOMATION_CONFIG.RULES.TEMPLATE_PLACEHOLDER') }}
          </option>
          <option
            v-if="rule.template && !templateOf(rule)"
            :value="rule.template"
          >
            {{ rule.template }}
          </option>
          <option v-for="tp in approved" :key="tp.name + tp.language" :value="tp.name">
            {{ tp.name }} · {{ tp.category }} · {{ tp.language }}
          </option>
        </select>
      </label>
      <p
        v-if="rule.template && !templateOf(rule)"
        class="mt-1 text-xs text-n-amber-11"
      >
        {{ t('CRM_AUTOMATION_CONFIG.RULES.TEMPLATE_MISSING') }}
      </p>
      <p
        v-if="templateOf(rule)"
        class="p-2 mt-2 text-xs whitespace-pre-wrap rounded-lg bg-n-alpha-1 text-n-slate-12"
      >
        {{ templateOf(rule).body }}
      </p>

      <label
        v-if="['IMAGE', 'VIDEO', 'DOCUMENT'].includes(templateOf(rule)?.header_format)"
        class="block mt-3 text-xs text-n-slate-11"
      >
        {{
          t('CRM_AUTOMATION_CONFIG.RULES.HEADER_MEDIA', {
            format: templateOf(rule).header_format.toLowerCase(),
          })
        }}
        <input
          v-model="rule.header_media_url"
          type="url"
          class="w-full mt-1"
          :class="input"
        />
      </label>

      <div v-if="(rule.variables || []).length" class="mt-3">
        <div class="text-xs text-n-slate-11">
          {{ t('CRM_AUTOMATION_CONFIG.RULES.VARIABLES') }}
        </div>
        <div
          v-for="(v, k) in rule.variables"
          :key="k"
          class="flex flex-wrap items-center gap-2 mt-1"
        >
          <span class="w-20 text-xs text-n-slate-11">
            {{ t('CRM_AUTOMATION_CONFIG.RULES.VARIABLE', { n: k + 1 }) }}
          </span>
          <select
            :value="kindOf(v.source)"
            :class="input"
            @change="setKind(v, $event.target.value)"
          >
            <option v-for="(label, key) in sources" :key="key" :value="key">
              {{ label }}
            </option>
            <option value="field">
              {{ t('CRM_AUTOMATION_CONFIG.RULES.SOURCE_FIELD') }}
            </option>
            <option value="text">
              {{ t('CRM_AUTOMATION_CONFIG.RULES.SOURCE_TEXT') }}
            </option>
          </select>
          <input
            v-if="['field', 'text'].includes(kindOf(v.source))"
            :value="argOf(v.source)"
            type="text"
            :placeholder="
              kindOf(v.source) === 'field'
                ? t('CRM_AUTOMATION_CONFIG.RULES.SOURCE_FIELD_PLACEHOLDER')
                : t('CRM_AUTOMATION_CONFIG.RULES.SOURCE_TEXT_PLACEHOLDER')
            "
            class="w-48"
            :class="input"
            @input="setArg(v, $event.target.value)"
          />
          <span class="text-xs text-n-slate-11">
            {{ t('CRM_AUTOMATION_CONFIG.RULES.FALLBACK') }}
          </span>
          <input
            v-model="v.fallback"
            type="text"
            class="flex-1 min-w-[8rem]"
            :class="input"
          />
        </div>
      </div>
    </section>

    <button
      type="button"
      class="self-start px-3 py-2 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
      @click="addRule"
    >
      {{ t('CRM_AUTOMATION_CONFIG.RULES.ADD') }}
    </button>
  </div>
</template>

<script setup>
// "Only for deals where" rows: a Zoho field, an operator and a value.
import { useI18n } from 'vue-i18n';

defineProps({
  ops: { type: Array, default: () => [] },
  inputClass: { type: String, default: '' },
});
const match = defineModel('match', { type: Array, default: () => [] });

const { t } = useI18n();

const add = () => {
  match.value = [
    ...(match.value || []),
    { field: '', op: 'contains', value: '' },
  ];
};
</script>

<template>
  <div>
    <div
      v-for="(m, j) in match"
      :key="j"
      class="flex flex-wrap items-center gap-2 mt-1"
    >
      <input
        v-model="m.field"
        type="text"
        :placeholder="t('CRM_AUTOMATION_CONFIG.RULES.FIELD_PLACEHOLDER')"
        class="w-48"
        :class="inputClass"
      />
      <select v-model="m.op" :class="inputClass">
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
        :class="inputClass"
      />
      <button
        type="button"
        class="px-2 text-xs text-n-slate-11 hover:text-n-ruby-11"
        @click="match.splice(j, 1)"
      >
        {{ t('CRM_AUTOMATION_CONFIG.RULES.REMOVE') }}
      </button>
    </div>
    <button type="button" class="mt-1 text-xs text-n-brand" @click="add">
      {{ t('CRM_AUTOMATION_CONFIG.RULES.ADD_FILTER') }}
    </button>
  </div>
</template>

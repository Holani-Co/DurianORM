<script setup>
// Manual overrides for the name and phone customers are given ({{2}} and {{3}}
// in the templates). Without a row, the bridge uses the owner's store in
// Settings → Stores, then the owner's Zoho user.
import { useI18n } from 'vue-i18n';

const team = defineModel('team', { type: Array, default: () => [] });
const { t } = useI18n();
const input =
  'px-2 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12';

const add = () => {
  if (!Array.isArray(team.value)) team.value = [];
  team.value.push({ owner: '', name: '', phone: '' });
};
</script>

<template>
  <div class="flex flex-col gap-2 max-w-2xl">
    <p class="text-xs text-n-slate-11">{{ t('CRM_AUTOMATION_CONFIG.TEAM.HINT') }}</p>
    <p v-if="!(team || []).length" class="text-sm text-n-slate-11">
      {{ t('CRM_AUTOMATION_CONFIG.TEAM.EMPTY') }}
    </p>
    <div
      v-for="(row, i) in team"
      :key="i"
      class="flex flex-wrap items-center gap-2"
    >
      <input
        v-model="row.owner"
        type="text"
        :placeholder="t('CRM_AUTOMATION_CONFIG.TEAM.OWNER')"
        class="flex-1 min-w-[12rem]"
        :class="input"
      />
      <input
        v-model="row.name"
        type="text"
        :placeholder="t('CRM_AUTOMATION_CONFIG.TEAM.NAME')"
        class="w-48"
        :class="input"
      />
      <input
        v-model="row.phone"
        type="tel"
        :placeholder="t('CRM_AUTOMATION_CONFIG.TEAM.PHONE')"
        class="w-48"
        :class="input"
      />
      <button
        type="button"
        class="px-2 py-1 text-xs rounded-lg text-n-ruby-11 hover:bg-n-alpha-2"
        @click="team.splice(i, 1)"
      >
        {{ t('CRM_AUTOMATION_CONFIG.TEAM.REMOVE') }}
      </button>
    </div>
    <button
      type="button"
      class="self-start px-3 py-2 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
      @click="add"
    >
      {{ t('CRM_AUTOMATION_CONFIG.TEAM.ADD') }}
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import ButtonV4 from 'dashboard/components-next/button/Button.vue';
import { useUISettings } from 'dashboard/composables/useUISettings';

const { uiSettings, updateUISettings } = useUISettings();

const isOpen = computed(
  () => !!uiSettings.value?.is_contact_sidebar_open
);

const toggle = () => {
  updateUISettings({ is_contact_sidebar_open: !isOpen.value });
};
</script>

<template>
  <ButtonV4
    v-tooltip="isOpen ? 'Hide contact details' : 'Show contact details'"
    size="sm"
    variant="ghost"
    color="slate"
    icon="i-lucide-user-round"
    class="rounded-md hover:bg-n-alpha-2"
    :class="{ 'bg-n-alpha-2 text-n-slate-12': isOpen }"
    :aria-pressed="isOpen"
    :aria-label="isOpen ? 'Hide contact details' : 'Show contact details'"
    @click="toggle"
  />
</template>

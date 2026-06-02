<script setup>
import ButtonV4 from 'dashboard/components-next/button/Button.vue';
import { usePlatformLink } from 'dashboard/composables/usePlatformLink';

const { platform } = usePlatformLink();

const openPlatform = () => {
  if (!platform.value?.url) return;
  window.open(platform.value.url, '_blank', 'noopener,noreferrer');
};
</script>

<template>
  <ButtonV4
    v-if="platform.url"
    v-tooltip="platform.label"
    size="sm"
    variant="ghost"
    color="slate"
    :icon="platform.icon"
    class="rounded-md hover:bg-n-alpha-2"
    :aria-label="platform.label"
    @click="openPlatform"
  />
</template>

<script setup>
/* global axios */
// Durian OS "View as" bar (shared View as spec 4). App.vue shows it while the session is a
// View as: the server marks the current user with impersonated_by and view_as_ends_at.
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import endPoints from 'dashboard/api/endPoints';
import {
  clearBrowserSessionCookies,
  clearLocalStorageOnLogout,
  clearSessionStorageOnLogout,
  deleteIndexedDBOnLogout,
} from 'dashboard/store/utils/api';

const STOP_URL = 'https://durianos.in/impersonate';

const { t } = useI18n();
const currentUser = useMapGetter('getCurrentUser');
const stopping = ref(false);
let endTimer = null;

const impersonatedBy = computed(() => currentUser.value?.impersonated_by);
const person = computed(
  () => currentUser.value?.name || currentUser.value?.email || ''
);
const admin = computed(
  () => impersonatedBy.value?.name || impersonatedBy.value?.email || ''
);

const stop = async () => {
  if (stopping.value) return;
  stopping.value = true;
  try {
    await axios.delete(endPoints('logout').url);
  } catch (error) {
    // The session may already have ended; leave anyway.
  } finally {
    deleteIndexedDBOnLogout();
    clearBrowserSessionCookies();
    clearLocalStorageOnLogout();
    clearSessionStorageOnLogout();
    window.location = STOP_URL;
  }
};

// The server ends the session at view_as_ends_at; leave the dashboard then too.
watch(
  () => currentUser.value?.view_as_ends_at,
  endsAt => {
    clearTimeout(endTimer);
    if (!impersonatedBy.value || !endsAt) return;
    const delay = Math.max(new Date(endsAt).getTime() - Date.now(), 0);
    endTimer = setTimeout(stop, Math.min(delay, 2147483647));
  },
  { immediate: true }
);

onBeforeUnmount(() => clearTimeout(endTimer));
</script>

<template>
  <div
    class="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 py-1.5 text-xs font-medium bg-n-amber-3 text-n-amber-12 border-b border-n-amber-6"
    role="status"
  >
    <span>{{ t('DURIAN_VIEW_AS.MESSAGE', { person, admin }) }}</span>
    <button
      type="button"
      class="px-2 py-0.5 rounded-md bg-n-amber-5 hover:bg-n-amber-6 font-semibold"
      :disabled="stopping"
      @click="stop"
    >
      {{ t('DURIAN_VIEW_AS.STOP') }}
    </button>
  </div>
</template>

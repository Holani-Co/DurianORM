<script setup>
// Durian — Offers admin screen. The client uploads the offer image they post
// on Instagram + a caption, sets priority + tags, and toggles active. The
// bridge reads the live offers to surface one on a customer's greeting.
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;
const base = () => `/api/v1/accounts/${accountId.value}/offers`;

const offers = ref([]);
const loading = ref(false);
const editing = ref(null);
const saving = ref(false);
const imageFile = ref(null);
const imagePreview = ref('');

const load = async () => {
  loading.value = true;
  try {
    offers.value = (await axios.get(base())).data;
  } catch {
    useAlert(t('OFFERS.FETCH_ERROR'));
  } finally {
    loading.value = false;
  }
};
onMounted(load);

const startAdd = () => {
  editing.value = {
    caption: '',
    priority: offers.value.length + 1,
    active: true,
    tags: [],
    expires_at: null,
  };
  imageFile.value = null;
  imagePreview.value = '';
};

const startEdit = o => {
  editing.value = { ...o, tags: [...(o.tags || [])] };
  imageFile.value = null;
  imagePreview.value = o.image_url || '';
};

const cancel = () => {
  editing.value = null;
  imageFile.value = null;
  imagePreview.value = '';
};

const onFile = e => {
  const f = e.target.files[0];
  if (!f) return;
  imageFile.value = f;
  imagePreview.value = URL.createObjectURL(f);
};

const tagsText = computed({
  get: () => (editing.value?.tags || []).join(', '),
  set: v => {
    if (editing.value) {
      editing.value.tags = v
        .split(',')
        .map(x => x.trim())
        .filter(Boolean);
    }
  },
});

const save = async () => {
  if (!editing.value.caption.trim()) {
    useAlert(t('OFFERS.CAPTION_REQUIRED'));
    return;
  }
  if (!editing.value.id && !imageFile.value) {
    useAlert(t('OFFERS.IMAGE_REQUIRED'));
    return;
  }
  saving.value = true;
  try {
    const fd = new FormData();
    fd.append('caption', editing.value.caption.trim());
    fd.append('priority', editing.value.priority ?? 0);
    fd.append('active', editing.value.active);
    (editing.value.tags || []).forEach(tag => fd.append('tags[]', tag));
    if (editing.value.expires_at)
      fd.append('expires_at', editing.value.expires_at);
    if (imageFile.value) fd.append('image', imageFile.value);
    if (editing.value.id)
      await axios.patch(`${base()}/${editing.value.id}`, fd);
    else await axios.post(base(), fd);
    cancel();
    load();
  } catch {
    useAlert(t('OFFERS.SAVE_ERROR'));
  } finally {
    saving.value = false;
  }
};

const toggleActive = async o => {
  try {
    await axios.patch(`${base()}/${o.id}`, { active: !o.active });
    load();
  } catch {
    useAlert(t('OFFERS.SAVE_ERROR'));
  }
};

const remove = async o => {
  // eslint-disable-next-line no-alert
  if (!window.confirm(t('OFFERS.DELETE_CONFIRM'))) return;
  try {
    await axios.delete(`${base()}/${o.id}`);
    load();
  } catch {
    useAlert(t('OFFERS.DELETE_ERROR'));
  }
};
</script>

<template>
  <div class="flex flex-col gap-4 p-6 overflow-y-auto">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h1 class="text-lg font-medium text-n-slate-12">
          {{ $t('OFFERS.HEADER') }}
        </h1>
        <p class="mt-1 text-sm text-n-slate-11">
          {{ $t('OFFERS.DESCRIPTION') }}
        </p>
      </div>
      <button
        type="button"
        class="shrink-0 flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-n-brand text-white hover:opacity-90"
        @click="startAdd"
      >
        <span class="size-3.5 i-lucide-plus" />
        {{ $t('OFFERS.ADD') }}
      </button>
    </div>

    <!-- Add / edit form -->
    <div
      v-if="editing"
      class="flex flex-col gap-4 p-5 shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2"
    >
      <div class="flex flex-col gap-1.5">
        <label class="text-sm font-medium text-n-slate-12">
          {{ $t('OFFERS.FORM.IMAGE') }}
        </label>
        <img
          v-if="imagePreview"
          :src="imagePreview"
          class="object-contain w-full max-h-56 rounded-lg bg-n-alpha-1"
        />
        <input type="file" accept="image/*" class="text-sm" @change="onFile" />
      </div>
      <div class="flex flex-col gap-1.5">
        <label class="text-sm font-medium text-n-slate-12">
          {{ $t('OFFERS.FORM.CAPTION') }}
        </label>
        <textarea
          v-model="editing.caption"
          rows="3"
          :placeholder="$t('OFFERS.FORM.CAPTION_PH')"
          class="px-3 py-2 text-sm rounded-lg outline-1 outline outline-n-weak bg-n-solid-1 text-n-slate-12"
        />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <label class="text-sm font-medium text-n-slate-12">
            {{ $t('OFFERS.FORM.PRIORITY') }}
          </label>
          <input
            v-model.number="editing.priority"
            type="number"
            min="1"
            class="px-3 py-2 text-sm rounded-lg outline-1 outline outline-n-weak bg-n-solid-1 text-n-slate-12"
          />
        </div>
        <div class="flex flex-col gap-1.5">
          <label class="text-sm font-medium text-n-slate-12">
            {{ $t('OFFERS.FORM.EXPIRES') }}
          </label>
          <input
            v-model="editing.expires_at"
            type="date"
            class="px-3 py-2 text-sm rounded-lg outline-1 outline outline-n-weak bg-n-solid-1 text-n-slate-12"
          />
        </div>
      </div>
      <div class="flex flex-col gap-1.5">
        <label class="text-sm font-medium text-n-slate-12">
          {{ $t('OFFERS.FORM.TAGS') }}
        </label>
        <input
          v-model="tagsText"
          :placeholder="$t('OFFERS.FORM.TAGS_PH')"
          class="px-3 py-2 text-sm rounded-lg outline-1 outline outline-n-weak bg-n-solid-1 text-n-slate-12"
        />
        <span class="text-xs text-n-slate-10">{{
          $t('OFFERS.FORM.TAGS_HINT')
        }}</span>
      </div>
      <label
        class="flex items-center gap-2 text-sm cursor-pointer text-n-slate-12"
      >
        <input
          v-model="editing.active"
          type="checkbox"
          class="accent-n-brand"
        />
        {{ $t('OFFERS.FORM.ACTIVE') }}
      </label>
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:bg-n-alpha-2"
          @click="cancel"
        >
          {{ $t('OFFERS.FORM.CANCEL') }}
        </button>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg bg-n-brand text-white hover:opacity-90 disabled:opacity-50"
          :disabled="saving"
          @click="save"
        >
          {{ $t('OFFERS.FORM.SAVE') }}
        </button>
      </div>
    </div>

    <!-- Offer list -->
    <div v-if="offers.length" class="flex flex-col gap-3">
      <div
        v-for="o in offers"
        :key="o.id"
        class="flex items-center gap-4 p-4 shadow outline-1 outline outline-n-container rounded-xl bg-n-solid-2"
        :class="{ 'opacity-60': !o.active }"
      >
        <img
          v-if="o.image_url"
          :src="o.image_url"
          class="object-cover rounded-lg size-16 bg-n-alpha-1"
        />
        <span
          v-else
          class="flex items-center justify-center rounded-lg size-16 bg-n-alpha-1 i-lucide-image text-n-slate-10"
        />
        <div class="flex-1 min-w-0">
          <p class="text-sm truncate text-n-slate-12">{{ o.caption }}</p>
          <p class="mt-1 text-xs text-n-slate-10">
            {{
              $t('OFFERS.PRIORITY_LABEL', { n: o.priority }) +
              (o.tags && o.tags.length ? ` · ${o.tags.join(', ')}` : '')
            }}
          </p>
        </div>
        <button
          type="button"
          class="shrink-0 px-2.5 py-1 text-xs rounded-md outline-1 outline outline-n-container text-n-slate-11 hover:bg-n-alpha-2"
          @click="toggleActive(o)"
        >
          {{ o.active ? $t('OFFERS.DEACTIVATE') : $t('OFFERS.ACTIVATE') }}
        </button>
        <button
          type="button"
          class="shrink-0 size-7 flex items-center justify-center rounded-md hover:bg-n-alpha-2 text-n-slate-11"
          @click="startEdit(o)"
        >
          <span class="size-4 i-lucide-pencil" />
        </button>
        <button
          type="button"
          class="shrink-0 size-7 flex items-center justify-center rounded-md hover:bg-n-alpha-2 text-n-ruby-11"
          @click="remove(o)"
        >
          <span class="size-4 i-lucide-trash-2" />
        </button>
      </div>
    </div>
    <p v-else-if="!loading && !editing" class="text-sm text-n-slate-10">
      {{ $t('OFFERS.EMPTY') }}
    </p>
  </div>
</template>

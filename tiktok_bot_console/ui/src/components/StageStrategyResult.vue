<template>
  <section class="strategy-card">
    <header><div><span>STRATEGY</span><h4>{{ t('pipeline.stageResults.strategyTitle') }}</h4></div></header>
    <p v-if="legacy" class="state">{{ t('pipeline.stageResults.legacy') }}</p>
    <p v-else-if="stageStatus === 'failed'" class="state error" role="alert">{{ t('pipeline.stageResults.failed') }}</p>
    <p v-else-if="loading" class="state">{{ t('pipeline.stageResults.loading') }}</p>
    <p v-else-if="error" class="state error" role="alert">{{ t('pipeline.stageResults.unavailable') }}</p>
    <template v-else-if="summary">
      <div class="metrics">
        <article v-for="item in metrics" :key="item.key"><span>{{ t(`pipeline.stageResults.strategy.${item.key}`) }}</span><strong>{{ item.value }}</strong></article>
      </div>
      <p v-if="summary.qualified === 0" class="state">{{ t('pipeline.stageResults.strategyNoQualified') }}</p>
      <p v-else-if="summary.drafts + summary.approved + summary.rejected === 0" class="state">{{ t('pipeline.stageResults.strategyEmpty') }}</p>
      <button v-if="summary.qualified > 0" type="button" class="primary" data-testid="open-strategy-review" @click="emit('open-workbench', jobId)">
        {{ t('pipeline.strategyWorkbench.open') }} <b v-if="summary.drafts">{{ summary.drafts }}</b>
      </button>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getAcquisitionStage03 } from '../api'
import type { AcquisitionStage03Summary, PipelineStageStatus } from '../types/pipeline'
const props = defineProps<{ jobId:string; stageStatus:PipelineStageStatus; legacy:boolean; refreshToken:number }>()
const emit = defineEmits<{(e:'open-workbench', jobId:string):void}>()
const { t } = useI18n(); const loading=ref(false); const error=ref(false); const summary=ref<AcquisitionStage03Summary|null>(null)
let generation=0; let controller:AbortController|null=null
const metrics=computed(()=>summary.value ? [
  {key:'qualified',value:summary.value.qualified},{key:'drafts',value:summary.value.drafts},{key:'approved',value:summary.value.approved},
  {key:'rejected',value:summary.value.rejected},{key:'missing',value:summary.value.missingStrategies},
] : [])
async function load(){ const g=++generation; controller?.abort(); controller=new AbortController(); summary.value=null; error.value=false
  if(props.legacy||props.stageStatus==='failed'||!props.jobId){loading.value=false;return} loading.value=true
  try { const {data}=await getAcquisitionStage03(props.jobId,controller.signal); if(g===generation) summary.value=data.summary }
  catch(e){ if(g===generation && !(e instanceof DOMException && e.name==='AbortError')) error.value=true }
  finally{if(g===generation)loading.value=false}
}
watch(()=>[props.jobId,props.refreshToken,props.legacy,props.stageStatus],load,{immediate:true})
onBeforeUnmount(()=>{generation++;controller?.abort()})
</script>
<style scoped>
.strategy-card{margin-top:12px;padding:16px;border:1px solid var(--border);border-radius:var(--card-radius);background:var(--surface)}
header{margin-bottom:14px}header span{color:var(--brand);font-family:var(--font-mono);font-size:10px;font-weight:700;letter-spacing:.12em}h4{margin:3px 0 0;color:var(--fg);font-size:15px}.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.metrics article{padding:10px 12px;border:1px solid var(--border);border-radius:9px;background:var(--bg)}.metrics span{display:block;color:var(--muted);font-size:11px}.metrics strong{display:block;margin-top:4px;color:var(--fg);font:700 18px var(--font-mono)}.state{margin:10px 0 0;padding:12px;border-radius:8px;background:var(--bg);color:var(--muted);font-size:12px}.error{background:var(--err-soft);color:var(--err)}.primary{min-height:44px;margin-top:14px;padding:9px 15px;border:1px solid var(--brand);border-radius:9px;background:var(--brand);color:white;font-weight:700;cursor:pointer}.primary b{margin-left:8px;padding:2px 7px;border-radius:999px;background:#ffffff33}@media(max-width:720px){.metrics{grid-template-columns:1fr 1fr}.primary{width:100%}}
</style>

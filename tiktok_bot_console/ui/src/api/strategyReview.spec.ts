import { beforeEach, describe, expect, it, vi } from 'vitest'
const { get, post, patch } = vi.hoisted(()=>({ get:vi.fn(), post:vi.fn(), patch:vi.fn() }))
vi.mock('axios',()=>({default:{create:vi.fn(()=>({get,post,patch,put:vi.fn(),delete:vi.fn(),interceptors:{request:{use:vi.fn()},response:{use:vi.fn()}}}))}}))
import { approveAcquisitionStrategiesBatch, approveAcquisitionStrategy, editAcquisitionStrategy, getAcquisitionStage03, getAcquisitionStrategy, listAcquisitionStrategies, rejectAcquisitionStrategy } from './index'
describe('strategy review API',()=>{beforeEach(()=>vi.clearAllMocks())
 it('uses encoded Job-scoped URLs, query filters and AbortSignal',async()=>{get.mockResolvedValue({data:{}});const signal=new AbortController().signal
  await getAcquisitionStage03('job /?',signal);await listAcquisitionStrategies('job /?',{reviewStatus:'draft',limit:12,offset:2},signal);await getAcquisitionStrategy('job /?',7,signal)
  expect(get).toHaveBeenNthCalledWith(1,'/api/acquisition/jobs/job%20%2F%3F/stage-03',{signal})
  expect(get).toHaveBeenNthCalledWith(2,'/api/acquisition/jobs/job%20%2F%3F/strategies',{params:{reviewStatus:'draft',limit:12,offset:2},signal})
  expect(get).toHaveBeenNthCalledWith(3,'/api/acquisition/jobs/job%20%2F%3F/strategies/7',{signal})
 })
 it('preserves strict CAS request bodies for every mutation',async()=>{post.mockResolvedValue({data:{}});patch.mockResolvedValue({data:{}});const signal=new AbortController().signal
  const edit={reviewVersion:2,persona:'buyer' as const,strategyType:'partnership' as const,commentTemplate:'hello',dmTemplate:'hi',actionPlan:'follow up',priority:2}
  await editAcquisitionStrategy('j',7,edit,signal);await approveAcquisitionStrategy('j',7,{reviewVersion:3},signal);await rejectAcquisitionStrategy('j',7,{reviewVersion:4,reason:'return'},signal);await approveAcquisitionStrategiesBatch('j',{items:[{strategyId:7,reviewVersion:5}]},signal)
  expect(patch).toHaveBeenCalledWith('/api/acquisition/jobs/j/strategies/7',edit,{signal})
  expect(post).toHaveBeenNthCalledWith(1,'/api/acquisition/jobs/j/strategies/7/approve',{reviewVersion:3},{signal})
  expect(post).toHaveBeenNthCalledWith(2,'/api/acquisition/jobs/j/strategies/7/reject',{reviewVersion:4,reason:'return'},{signal})
  expect(post).toHaveBeenNthCalledWith(3,'/api/acquisition/jobs/j/strategies/approve-batch',{items:[{strategyId:7,reviewVersion:5}]},{signal})
 })
})

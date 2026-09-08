import {describe,it,expect} from 'vitest';
import {boundedExpanded,bytes} from './utils';
describe('bounded reader',()=>{it('evicts oldest expanded round and frees it',()=>expect(boundedExpanded([4,5,6],1)).toEqual([5,6,1]));it('collapses an existing round',()=>expect(boundedExpanded([4,5,6],5)).toEqual([4,6]));it('keeps at most three across a long session',()=>{let state:number[]=[];for(let n=0;n<100000;n++)state=boundedExpanded(state,n);expect(state).toEqual([99997,99998,99999])})});
describe('size formatting',()=>it('shows binary megabytes',()=>expect(bytes(5242880)).toEqual('5.0 MB')));

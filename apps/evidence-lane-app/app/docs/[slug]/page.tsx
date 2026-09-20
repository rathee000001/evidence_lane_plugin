import {notFound} from 'next/navigation';
import {guides} from '../../data/guides';
import {GuideArticle} from '../../components/guide-article';
export const dynamicParams=false;
export function generateStaticParams(){return guides.map(g=>({slug:g.slug}))}
export async function generateMetadata({params}:{params:Promise<{slug:string}>}){const{slug}=await params;return{title:guides.find(g=>g.slug===slug)?.title??'Guide'}}
export default async function GuidePage({params}:{params:Promise<{slug:string}>}){const{slug}=await params;const guide=guides.find(g=>g.slug===slug);if(!guide)notFound();return <GuideArticle guide={guide}/>}

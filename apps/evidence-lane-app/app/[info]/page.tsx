import {notFound} from 'next/navigation';import {siteInformation} from '../data/site-information';import {GuideArticle} from '../components/guide-article';
export const dynamicParams=false;
export function generateStaticParams(){return siteInformation.map(p=>({info:p.slug}))}
export async function generateMetadata({params}:{params:Promise<{info:string}>}){const{info}=await params;return{title:siteInformation.find(p=>p.slug===info)?.title??'Project information'}}
export default async function InformationPage({params}:{params:Promise<{info:string}>}){const{info}=await params;const guide=siteInformation.find(p=>p.slug===info);if(!guide)notFound();return <GuideArticle guide={guide}/>}

-- Supabase Storage setup for B2B Fitout document uploads
-- Run in Supabase Dashboard → SQL Editor (https://supabase.com/dashboard)

-- 1. Create public bucket (free tier: 1 GB storage)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'project-documents',
  'project-documents',
  true,
  52428800,  -- 50 MB per file
  null
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit;

-- 2. Policies: anon key from the dashboard app (Firebase Auth gates the UI)
-- For a stricter setup later, use Supabase Auth or Edge Functions with Firebase JWT.

drop policy if exists "project-documents public read" on storage.objects;
create policy "project-documents public read"
on storage.objects for select
using ( bucket_id = 'project-documents' );

drop policy if exists "project-documents anon insert" on storage.objects;
create policy "project-documents anon insert"
on storage.objects for insert
with check ( bucket_id = 'project-documents' );

drop policy if exists "project-documents anon update" on storage.objects;
create policy "project-documents anon update"
on storage.objects for update
using ( bucket_id = 'project-documents' );

drop policy if exists "project-documents anon delete" on storage.objects;
create policy "project-documents anon delete"
on storage.objects for delete
using ( bucket_id = 'project-documents' );

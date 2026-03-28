<<<<<<< HEAD
# Meeting MOM Generator

A web application that integrates with Microsoft Teams to fetch meeting transcripts, let migration managers edit and organize them into Minutes of Meeting (MOM), generate professional Word documents, and email them directly to customers.

## Features

- **Microsoft SSO** - Sign in with your Microsoft 365 account
- **Smart Meeting Filtering** - Automatically identifies customer meetings by detecting external attendees (non-CloudFuze domains)
- **Transcript Fetching** - Pulls meeting transcripts directly from Microsoft Teams via the Graph API
- **Editable Transcripts** - Review and edit transcripts before generating the MOM
- **Structured MOM Builder** - Organize content into Summary, Discussion Points, Action Items, and Decisions
- **Word Document Generation** - Creates a professionally formatted `.docx` file
- **Email Delivery** - Send the MOM document as an attachment directly from your Outlook account

## Prerequisites

- Python 3.9+
- A Microsoft 365 organization account (CloudFuze)
- An Azure AD App Registration (see setup below)
- Teams transcription enabled in your organization's Teams admin center

## Setup

### 1. Azure AD App Registration

1. Go to [Azure Portal](https://portal.azure.com) > **Azure Active Directory** > **App registrations** > **New registration**
2. Set the **Name** to `Meeting MOM Generator`
3. Set **Supported account types** to "Accounts in this organizational directory only (Single tenant)"
4. Set the **Redirect URI** to `Web` > `http://localhost:5000/auth/callback`
5. Click **Register**

After registration:
1. Copy the **Application (client) ID** and **Directory (tenant) ID** from the Overview page
2. Go to **Certificates & secrets** > **New client secret** > copy the secret value
3. Go to **API permissions** > **Add a permission** > **Microsoft Graph** > **Delegated permissions** and add:
   - `User.Read`
   - `Calendars.Read`
   - `OnlineMeeting.Read`
   - `OnlineMeetingTranscript.Read.All`
   - `Mail.Send`
4. Click **Grant admin consent** (requires admin approval)

### 2. Configure the Application

```bash
# Clone/navigate to the project
cd Meeting_MOMs

# Copy the env template and fill in your values
copy .env.example .env
```

Edit `.env` with your Azure AD credentials:
```
AZURE_CLIENT_ID=<your-client-id>
AZURE_CLIENT_SECRET=<your-client-secret>
AZURE_TENANT_ID=<your-tenant-id>
FLASK_SECRET_KEY=<generate-a-random-key>
ORG_DOMAIN=cloudfuze.com
```

### 3. Install Dependencies

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 4. Run the Application

```bash
python run.py
```

Open your browser at `http://localhost:5000`

## How It Works

1. **Sign in** with your Microsoft 365 account
2. **Select a date range** on the dashboard - the app fetches your Teams meetings and filters to show only those with external (customer) attendees
3. **Click "View Transcript"** on a meeting to fetch and display the Teams transcript
4. **Edit the transcript** if needed, then proceed to the MOM Builder
5. **Fill in the MOM sections**: Summary, Discussion Points, Action Items, Decisions
6. **Preview and download** the Word document, or **send it directly** to the customer's email

## Meeting Filtering Logic

The app identifies "customer meetings" using a multi-layered approach:

1. Fetches only online (Teams) meetings from your calendar
2. Checks each meeting's attendee list for email domains outside your organization (`@cloudfuze.com`)
3. Optionally filters by meeting subject keywords (configurable in `.env`)
4. Presents a filtered list for the migration manager to manually select from

This means only the signed-in manager's meetings are visible, and they explicitly choose which ones to process. No other employees are affected.

## Important Notes

- **Transcription must be enabled** in your Teams admin center for transcripts to be available
- **Only scheduled meetings** (calendar-backed) support transcript retrieval via the Graph API
- Each manager sees **only their own meetings** - the app uses delegated (per-user) authentication
- The Word document is generated in-memory and never stored on disk
=======
# Meeting_MOMs
>>>>>>> b336e0c68dbea5825df9aff6e000446c099cc2ae

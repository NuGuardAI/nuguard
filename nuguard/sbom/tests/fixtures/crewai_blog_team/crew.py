"""Content creation crew using CrewAI."""
from crewai import Agent, Crew, Task
from crewai_tools import SerperDevTool, WebsiteSearchTool

search_tool = SerperDevTool()
web_tool = WebsiteSearchTool()

RESEARCHER_BACKSTORY = """You are a veteran tech journalist with 15 years of experience
covering AI and machine learning. You have a knack for finding the most relevant and
accurate information quickly. Your research is thorough and always well-sourced."""

WRITER_BACKSTORY = """You are an award-winning technical writer who can transform
complex AI concepts into engaging, accessible prose. Your articles consistently achieve
high engagement and are frequently shared in the AI community."""

researcher = Agent(
    role="Senior AI Researcher",
    goal="Find and summarize the latest developments in the specified AI topic.",
    backstory=RESEARCHER_BACKSTORY,
    tools=[search_tool, web_tool],
    llm="claude-3-5-sonnet-20241022",
    verbose=True,
)

writer = Agent(
    role="Technical Content Writer",
    goal="Write a compelling, accurate blog post based on the research.",
    backstory=WRITER_BACKSTORY,
    llm="gpt-4o",
    verbose=True,
)

research_task = Task(
    description="Research the latest developments in {topic} and produce a structured summary.",
    expected_output="A structured research summary with key findings and citations.",
    agent=researcher,
)

writing_task = Task(
    description="Write a 1000-word blog post based on the research summary.",
    expected_output="A polished blog post ready for publication.",
    agent=writer,
    context=[research_task],
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    verbose=True,
)

FROM karakum-base:latest

USER root

# Node 22 for Claude Code
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code
RUN npm install -g @anthropic-ai/claude-code

USER agent
WORKDIR /home/agent
CMD ["bash"]
